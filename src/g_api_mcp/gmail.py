"""
Gmail MCP tools.

Tools
-----
gmail_list_messages   — thin message list (IDs + metadata, no body)
gmail_get_message     — full content for one message
gmail_send_message    — compose and send
gmail_create_draft    — save to Drafts
gmail_list_labels     — all labels for the account
gmail_modify_message  — add/remove labels (mark read, star, etc.)
gmail_get_attachment  — download an attachment to a local path
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from mcp.server.fastmcp.exceptions import ToolError

from g_api_mcp.auth import cred_manager
from g_api_mcp.envelope import build_envelope, error_envelope
from g_api_mcp.server import mcp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _gmail_service():
    creds = await asyncio.to_thread(cred_manager.get_valid_credentials)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _http_error_message(e: HttpError) -> str:
    try:
        detail = json.loads(e.content.decode()).get("error", {}).get("message", str(e))
    except Exception:
        detail = str(e)
    return f"Google API error {e.resp.status}: {detail}"


def _decode_body(data: str) -> bytes:
    """Decode a Gmail base64url-encoded body part (adds missing padding)."""
    padded = data + "=" * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _extract_body(payload: dict) -> tuple[str | None, str | None]:
    """
    Walk the MIME tree and return (text/plain, text/html).
    Returns (None, None) if neither part is found.
    """
    text: str | None = None
    html: str | None = None

    def walk(part: dict) -> None:
        nonlocal text, html
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        filename = part.get("filename", "")

        if mime == "text/plain" and not filename and text is None:
            raw = body.get("data", "")
            if raw:
                text = _decode_body(raw).decode("utf-8", errors="replace")
        elif mime == "text/html" and not filename and html is None:
            raw = body.get("data", "")
            if raw:
                html = _decode_body(raw).decode("utf-8", errors="replace")

        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    return text, html


def _extract_attachments(payload: dict) -> list[dict]:
    """Return attachment metadata (no binary data) from a FULL message payload."""
    attachments: list[dict] = []

    def walk(part: dict) -> None:
        body = part.get("body", {})
        filename = part.get("filename", "")
        if filename and body.get("attachmentId"):
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size_bytes": body.get("size", 0),
                    "attachment_id": body["attachmentId"],
                    "part_id": part.get("partId", ""),
                }
            )
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    return attachments


def _to_thin_message(raw: dict) -> dict:
    """Collapse a METADATA-format Gmail message to a minimal summary."""
    headers: dict[str, str] = {
        h["name"]: h["value"]
        for h in raw.get("payload", {}).get("headers", [])
    }
    has_attachments = any(
        p.get("filename")
        for p in raw.get("payload", {}).get("parts", [])
    )
    return {
        "id": raw["id"],
        "thread_id": raw.get("threadId"),
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": raw.get("snippet", ""),
        "labels": raw.get("labelIds", []),
        "has_attachments": has_attachments,
        "size_estimate": raw.get("sizeEstimate", 0),
    }


def _build_raw_message(
    *,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str],
    bcc: list[str],
    reply_to_message_id: str | None,
    reply_thread_references: str | None,
) -> str:
    """Build a base64url-encoded RFC 2822 message string."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    if reply_to_message_id:
        msg["In-Reply-To"] = reply_to_message_id
        refs = reply_thread_references or reply_to_message_id
        msg["References"] = refs
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def gmail_list_messages(
    query: str = "",
    max_results: int = 20,
    page_cursor: str | None = None,
    label_ids: list[str] | None = None,
) -> str:
    """List Gmail messages matching a query. Returns thin summaries only — no body content.
    Use gmail_get_message to read full content for specific IDs.

    Args:
        query: Gmail search syntax. Examples:
               "is:unread in:inbox"
               "from:alice@example.com subject:budget"
               "after:2025/01/01 has:attachment"
               "label:work newer_than:7d"
        max_results: Number of messages per page (1–100). Default 20.
        page_cursor: Pass pagination.next_cursor from a previous call to get the next page.
        label_ids: Filter by label IDs, e.g. ["INBOX", "IMPORTANT"].
    """
    max_results = max(1, min(max_results, 100))

    try:
        service = await _gmail_service()
        kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": max_results,
            "q": query,
            "fields": "messages(id,threadId),nextPageToken,resultSizeEstimate",
        }
        if page_cursor:
            kwargs["pageToken"] = page_cursor
        if label_ids:
            kwargs["labelIds"] = label_ids

        list_resp = await asyncio.to_thread(
            lambda: service.users().messages().list(**kwargs).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    stubs = list_resp.get("messages", [])
    has_more = "nextPageToken" in list_resp
    total_estimate = list_resp.get("resultSizeEstimate")

    thin_messages: list[dict] = []

    if stubs:
        # Batch-fetch METADATA (headers only, no body) for all stubs in one HTTP round trip.
        # Batch limit is 100 calls; max_results is capped at 100, so one batch suffices.
        batch_results: dict[str, dict] = {}
        batch = service.new_batch_http_request()

        def make_cb(msg_id: str):
            def cb(req_id: str, resp: dict | None, err: Any) -> None:
                if err is None and resp is not None:
                    batch_results[msg_id] = resp
                else:
                    log.warning("Batch fetch failed for message %s: %s", msg_id, err)
            return cb

        for stub in stubs:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=stub["id"],
                    format="METADATA",
                    metadataHeaders=["From", "Subject", "Date"],
                    fields="id,threadId,snippet,internalDate,labelIds,sizeEstimate,payload/headers,payload/parts(filename)",
                ),
                callback=make_cb(stub["id"]),
            )

        await asyncio.to_thread(batch.execute)

        for stub in stubs:
            raw = batch_results.get(stub["id"])
            if raw:
                thin_messages.append(_to_thin_message(raw))

    env = build_envelope(
        data=thin_messages,
        has_more=has_more,
        next_cursor=list_resp.get("nextPageToken"),
        total_estimate=total_estimate,
    )
    return json.dumps(env)


@mcp.tool()
async def gmail_get_message(
    message_id: str,
    include_html: bool = False,
) -> str:
    """Fetch full content for a single Gmail message.
    Returns body text, headers, and attachment metadata (not attachment binary data).
    Token cost varies widely (500–15,000+) depending on message length — check
    context_hint.estimated_tokens in the response.

    Args:
        message_id: Gmail message ID (from gmail_list_messages results).
        include_html: Also include body_html field. Default false — body_text is
                      usually sufficient and HTML adds significant token cost.
    """
    try:
        service = await _gmail_service()
        raw = await asyncio.to_thread(
            lambda: service.users().messages().get(
                userId="me",
                id=message_id,
                format="FULL",
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    payload = raw.get("payload", {})
    headers: dict[str, str] = {h["name"]: h["value"] for h in payload.get("headers", [])}
    body_text, body_html = _extract_body(payload)
    attachments = _extract_attachments(payload)

    data: dict[str, Any] = {
        "id": raw["id"],
        "thread_id": raw.get("threadId"),
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "date": headers.get("Date", ""),
        "labels": raw.get("labelIds", []),
        "snippet": raw.get("snippet", ""),
        "body_text": body_text,
        "attachments": attachments,
    }
    if include_html:
        data["body_html"] = body_html

    env = build_envelope(data=data, is_list=False)
    return json.dumps(env)


@mcp.tool()
async def gmail_send_message(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to_message_id: str | None = None,
) -> str:
    """Compose and send a Gmail message.

    Args:
        to: Recipient email addresses.
        subject: Message subject.
        body: Plain-text message body.
        cc: CC recipients (optional).
        bcc: BCC recipients (optional).
        reply_to_message_id: Set to thread a reply. Use the `id` field from
                              gmail_get_message. Sets In-Reply-To and References
                              headers and attaches to the same thread.
    """
    thread_id: str | None = None
    references: str | None = None

    if reply_to_message_id:
        try:
            service = await _gmail_service()
            orig = await asyncio.to_thread(
                lambda: service.users().messages().get(
                    userId="me",
                    id=reply_to_message_id,
                    format="METADATA",
                    metadataHeaders=["Message-ID", "References"],
                ).execute()
            )
            orig_headers = {
                h["name"]: h["value"]
                for h in orig.get("payload", {}).get("headers", [])
            }
            thread_id = orig.get("threadId")
            existing_refs = orig_headers.get("References", "")
            orig_msg_id = orig_headers.get("Message-ID", reply_to_message_id)
            references = (existing_refs + " " + orig_msg_id).strip()
        except HttpError as e:
            return error_envelope(_http_error_message(e))

    raw = _build_raw_message(
        to=to,
        subject=subject,
        body=body,
        cc=cc or [],
        bcc=bcc or [],
        reply_to_message_id=reply_to_message_id,
        reply_thread_references=references,
    )

    send_body: dict[str, Any] = {"raw": raw}
    if thread_id:
        send_body["threadId"] = thread_id

    try:
        service = await _gmail_service()
        result = await asyncio.to_thread(
            lambda: service.users().messages().send(
                userId="me", body=send_body
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={"message_id": result["id"], "thread_id": result.get("threadId")},
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def gmail_create_draft(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> str:
    """Save a composed message to Gmail Drafts without sending.

    Args:
        to: Recipient email addresses.
        subject: Message subject.
        body: Plain-text message body.
        cc: CC recipients (optional).
        bcc: BCC recipients (optional).
    """
    raw = _build_raw_message(
        to=to,
        subject=subject,
        body=body,
        cc=cc or [],
        bcc=bcc or [],
        reply_to_message_id=None,
        reply_thread_references=None,
    )

    try:
        service = await _gmail_service()
        result = await asyncio.to_thread(
            lambda: service.users().drafts().create(
                userId="me",
                body={"message": {"raw": raw}},
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={
            "draft_id": result["id"],
            "message_id": result.get("message", {}).get("id"),
        },
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def gmail_list_labels() -> str:
    """Return all Gmail labels for this account (system labels and user-created labels).
    Useful for resolving label IDs before calling gmail_modify_message.
    """
    try:
        service = await _gmail_service()
        result = await asyncio.to_thread(
            lambda: service.users().labels().list(userId="me").execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    labels = [
        {"id": lbl["id"], "name": lbl["name"], "type": lbl.get("type", "user")}
        for lbl in result.get("labels", [])
    ]
    env = build_envelope(data=labels, result_count=len(labels), has_more=False)
    return json.dumps(env)


@mcp.tool()
async def gmail_modify_message(
    message_id: str,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> str:
    """Add or remove labels on a Gmail message.
    Common uses: mark as read (remove_labels=["UNREAD"]), star (add_labels=["STARRED"]),
    archive (remove_labels=["INBOX"]), mark important (add_labels=["IMPORTANT"]).

    Args:
        message_id: Gmail message ID.
        add_labels: Label IDs to apply. Use gmail_list_labels to find IDs.
        remove_labels: Label IDs to remove.
    """
    if not add_labels and not remove_labels:
        raise ToolError("Provide at least one of add_labels or remove_labels.")

    try:
        service = await _gmail_service()
        result = await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me",
                id=message_id,
                body={
                    "addLabelIds": add_labels or [],
                    "removeLabelIds": remove_labels or [],
                },
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={"message_id": result["id"], "labels": result.get("labelIds", [])},
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def gmail_get_attachment(
    message_id: str,
    attachment_id: str,
    filename: str,
    save_path: str,
) -> str:
    """Download a Gmail attachment and save it to a local path.
    Attachment IDs come from the `attachments` array in gmail_get_message responses.

    Args:
        message_id: Parent message ID.
        attachment_id: The attachment_id from the gmail_get_message attachments list.
        filename: Original filename (used only for informational purposes).
        save_path: Absolute local filesystem path where the file should be saved.
    """
    try:
        service = await _gmail_service()
        result = await asyncio.to_thread(
            lambda: service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    raw_bytes = _decode_body(result["data"])
    out = Path(save_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw_bytes)

    env = build_envelope(
        data={"saved_to": str(out), "filename": filename, "size_bytes": len(raw_bytes)},
        is_list=False,
    )
    return json.dumps(env)
