"""
One-time OAuth2 setup. Run this before starting the MCP server.

    python auth_setup.py

Opens a browser window for Google consent. On completion, saves the refresh
token to Windows Credential Locker (keyring). The server process never opens
a browser — it only loads and refreshes the stored token.

To re-authenticate (e.g., after a 7-day Testing-mode expiry):
    python auth_setup.py
"""

import json
import sys
from pathlib import Path


import keyring
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

CLIENT_SECRETS = Path(__file__).parent / "client_secrets.json"
KEYRING_SERVICE = "g-api-mcp"
KEYRING_USERNAME = "oauth_token"


def main() -> None:
    if not CLIENT_SECRETS.exists():
        print(
            "ERROR: client_secrets.json not found.\n"
            "Download it from Google Cloud Console:\n"
            "  APIs & Services → Credentials → OAuth 2.0 Client IDs → Desktop app → Download JSON\n"
            f"Save it as: {CLIENT_SECRETS}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Opening browser for Google OAuth2 consent...")
    print("(If the browser does not open automatically, check the URL printed below.)\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    # port=0 → OS assigns a free port; avoids conflicts if a port is already in use
    creds = flow.run_local_server(port=0)

    # Strip client_id and client_secret from stored token — they are read
    # fresh from client_secrets.json on every server start, so rotating the
    # secret in Google Cloud Console takes effect without re-running this script.
    token_data = json.loads(creds.to_json())
    token_data.pop("client_id", None)
    token_data.pop("client_secret", None)
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, json.dumps(token_data))
    print("\nAuth complete. Credentials saved to Windows Credential Locker.")
    print("You can now start the MCP server.")


if __name__ == "__main__":
    main()
