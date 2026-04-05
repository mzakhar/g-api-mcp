"""
GoogleCredentialManager — loads the OAuth2 token from keyring and refreshes it as
needed. Thread-safe. Never opens a browser; raises RuntimeError if credentials are
missing or the refresh token is dead (user must re-run auth_setup.py).

Client secret rotation
----------------------
The stored keyring token contains only the refresh_token. client_id and client_secret
are read fresh from client_secrets.json on every credential construction so that
rotating the secret in Google Cloud Console (and replacing client_secrets.json) takes
effect without re-running auth_setup.py.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import keyring
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

log = logging.getLogger(__name__)

KEYRING_SERVICE = "g-api-mcp"
KEYRING_USERNAME = "oauth_token"
# Only the refresh_token is stored in keyring — client identity comes from disk
KEYRING_TOKEN_FIELDS = {"refresh_token", "scopes", "expiry", "token"}

CLIENT_SECRETS_PATH = Path(__file__).parent.parent.parent / "client_secrets.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]


def _load_client_identity() -> tuple[str, str]:
    """Read client_id and client_secret from client_secrets.json on disk.
    Called on every credential construction so secret rotation is picked up
    without re-running auth_setup.py.
    """
    if not CLIENT_SECRETS_PATH.exists():
        raise RuntimeError(
            f"client_secrets.json not found at {CLIENT_SECRETS_PATH}.\n"
            "Download it from Google Cloud Console and place it in the project root."
        )
    raw = json.loads(CLIENT_SECRETS_PATH.read_text())
    app = raw.get("installed") or raw.get("web")
    if not app:
        raise RuntimeError("client_secrets.json has no 'installed' or 'web' key.")
    return app["client_id"], app["client_secret"]


class GoogleCredentialManager:
    """Thread-safe credential holder. Call get_valid_credentials() from tool handlers."""

    def __init__(self) -> None:
        self._creds: Credentials | None = None
        self._lock = threading.Lock()

    def get_valid_credentials(self) -> Credentials:
        """Return valid credentials, refreshing the access token if expired."""
        with self._lock:
            if self._creds is None:
                self._load()

            if not self._creds.valid:
                if self._creds.expired and self._creds.refresh_token:
                    log.info("Access token expired, refreshing...")
                    # Re-read client identity from disk so a rotated secret is picked up.
                    # Rebuild the Credentials object so client_id/client_secret are fresh
                    # (they are read-only properties on an existing Credentials instance).
                    client_id, client_secret = _load_client_identity()
                    self._creds = Credentials(
                        token=self._creds.token,
                        refresh_token=self._creds.refresh_token,
                        token_uri=self._creds.token_uri,
                        client_id=client_id,
                        client_secret=client_secret,
                        scopes=self._creds.scopes,
                    )
                    try:
                        self._creds.refresh(Request())
                        # Refresh token may rotate — persist updated token
                        self._save()
                        log.info("Token refreshed successfully.")
                    except RefreshError as e:
                        raise RuntimeError(
                            f"Google refresh token is invalid or expired: {e}\n"
                            "Run: python auth_setup.py"
                        ) from e
                else:
                    raise RuntimeError(
                        "Google credentials are invalid. Run: python auth_setup.py"
                    )

            return self._creds

    def _load(self) -> None:
        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if not stored:
            raise RuntimeError(
                "No Google credentials found in keyring.\n"
                "Run: python auth_setup.py"
            )
        token_data = json.loads(stored)
        # Always pull client identity from disk — not from the stored token
        client_id, client_secret = _load_client_identity()
        token_data["client_id"] = client_id
        token_data["client_secret"] = client_secret
        token_data.setdefault("token_uri", "https://oauth2.googleapis.com/token")
        self._creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        log.info("Credentials loaded (client identity from client_secrets.json).")

    def _save(self) -> None:
        """Persist the token, deliberately stripping client_id and client_secret
        so they are never stale in storage."""
        data = json.loads(self._creds.to_json())
        data.pop("client_id", None)
        data.pop("client_secret", None)
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, json.dumps(data))
        log.debug("Updated token saved to keyring (client identity not stored).")


# Module-level singleton — imported by tool modules
cred_manager = GoogleCredentialManager()
