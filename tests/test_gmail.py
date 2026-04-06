"""
Tests for Gmail tool handlers.

Google API calls are mocked at the service level so no real credentials
or network access are required.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp import gmail as gmail_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_stub(msg_id: str, thread_id: str | None = None) -> dict:
    return {"id": msg_id, "threadId": thread_id or msg_id}


def make_metadata_message(
    msg_id: str,
    subject: str = "Test Subject",
    from_: str = "sender@example.com",
    date: str = "Sat, 5 Apr 2025 10:00:00 -0500",
    labels: list[str] | None = None,
    snippet: str = "snippet text",
    has_attachment: bool = False,
) -> dict:
    parts = [{"filename": "file.pdf", "body": {}}] if has_attachment else []
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": labels or ["INBOX"],
        "snippet": snippet,
        "sizeEstimate": 1024,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": from_},
                {"name": "Date", "value": date},
            ],
            "parts": parts,
        },
    }


def make_full_message(msg_id: str, body_text: str = "Hello world") -> dict:
    encoded = base64.urlsafe_b64encode(body_text.encode()).decode()
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body_text[:50],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Full Message"},
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bob@example.com"},
                {"name": "Date", "value": "Sat, 5 Apr 2025 10:00:00 -0500"},
            ],
            "body": {"size": len(body_text), "data": encoded},
            "parts": [],
        },
    }


def _make_mock_service(
    list_response: dict,
    batch_messages: list[dict],
) -> MagicMock:
    """Build a mock gmail service that returns controlled responses."""
    service = MagicMock()

    # messages().list()
    service.users().messages().list().execute.return_value = list_response

    # batch request — calls each callback immediately with the staged message
    def fake_batch_execute():
        for msg in batch_messages:
            # The batch stores callbacks keyed by request; we trigger them all
            pass  # callbacks are injected via add() below

    batch = MagicMock()
    _batch_callbacks: dict[str, tuple] = {}

    def batch_add(request, callback=None):
        # Extract the message id from the request mock (we store it by order)
        _batch_callbacks[len(_batch_callbacks)] = (callback, batch_messages[len(_batch_callbacks)] if len(_batch_callbacks) < len(batch_messages) else None)

    def batch_execute():
        for idx, (cb, msg) in _batch_callbacks.items():
            if cb and msg:
                cb(str(idx), msg, None)

    batch.add.side_effect = batch_add
    batch.execute.side_effect = batch_execute
    service.new_batch_http_request.return_value = batch

    return service


# ---------------------------------------------------------------------------
# gmail_list_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_list_messages_returns_thin_summaries():
    stubs = [make_stub("msg1"), make_stub("msg2")]
    metadata = [
        make_metadata_message("msg1", subject="Hello", from_="alice@example.com"),
        make_metadata_message("msg2", subject="World", from_="bob@example.com"),
    ]
    list_resp = {"messages": stubs, "resultSizeEstimate": 2}
    mock_service = _make_mock_service(list_resp, metadata)

    with patch.object(gmail_module, "_gmail_service", return_value=mock_service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_list_messages(query="is:unread", max_results=10)

    env = json.loads(result)
    assert env["success"] is True
    assert env["pagination"]["result_count"] == 2
    assert env["pagination"]["has_more"] is False
    # Thin messages must not contain body content
    for msg in env["data"]:
        assert "body_text" not in msg
        assert "body_html" not in msg
        assert "subject" in msg
        assert "from" in msg


@pytest.mark.asyncio
async def test_gmail_list_messages_pagination():
    stubs = [make_stub("msg1")]
    metadata = [make_metadata_message("msg1")]
    list_resp = {"messages": stubs, "nextPageToken": "tok_next", "resultSizeEstimate": 100}
    mock_service = _make_mock_service(list_resp, metadata)

    with patch.object(gmail_module, "_gmail_service", return_value=mock_service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_list_messages()

    env = json.loads(result)
    assert env["pagination"]["has_more"] is True
    assert env["pagination"]["next_cursor"] == "tok_next"
    assert env["pagination"]["total_estimate"] == 100


@pytest.mark.asyncio
async def test_gmail_list_messages_empty_query():
    list_resp = {}  # no 'messages' key when query matches nothing
    mock_service = _make_mock_service(list_resp, [])

    with patch.object(gmail_module, "_gmail_service", return_value=mock_service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_list_messages(query="zzznonexistent")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"] == []
    assert env["pagination"]["result_count"] == 0


@pytest.mark.asyncio
async def test_gmail_list_messages_max_results_capped():
    """max_results > 100 should be silently clamped to 100."""
    list_resp = {"messages": [], "resultSizeEstimate": 0}
    mock_service = _make_mock_service(list_resp, [])
    captured = {}

    original_list = mock_service.users().messages().list

    def capture_list(**kwargs):
        captured.update(kwargs)
        return original_list(**kwargs)

    mock_service.users().messages().list.side_effect = capture_list

    with patch.object(gmail_module, "_gmail_service", return_value=mock_service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await gmail_module.gmail_list_messages(max_results=999)

    # The service was called with at most 100
    assert mock_service.users().messages().list.call_args.kwargs.get("maxResults", 100) <= 100


@pytest.mark.asyncio
async def test_gmail_list_messages_has_attachment_flag():
    stubs = [make_stub("msg1")]
    metadata = [make_metadata_message("msg1", has_attachment=True)]
    list_resp = {"messages": stubs, "resultSizeEstimate": 1}
    mock_service = _make_mock_service(list_resp, metadata)

    with patch.object(gmail_module, "_gmail_service", return_value=mock_service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_list_messages()

    env = json.loads(result)
    assert env["data"][0]["has_attachments"] is True


# ---------------------------------------------------------------------------
# gmail_get_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_get_message_returns_body():
    full = make_full_message("msg1", body_text="Hello from Alice")
    service = MagicMock()
    service.users().messages().get().execute.return_value = full

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_get_message(message_id="msg1")

    env = json.loads(result)
    assert env["success"] is True
    assert env["pagination"] is None  # singleton
    assert env["data"]["body_text"] == "Hello from Alice"
    assert env["data"]["subject"] == "Full Message"
    assert "body_html" not in env["data"]  # not requested


@pytest.mark.asyncio
async def test_gmail_get_message_include_html():
    full = make_full_message("msg1")
    service = MagicMock()
    service.users().messages().get().execute.return_value = full

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_get_message(message_id="msg1", include_html=True)

    env = json.loads(result)
    assert "body_html" in env["data"]


# ---------------------------------------------------------------------------
# gmail_list_labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_list_labels():
    service = MagicMock()
    service.users().labels().list().execute.return_value = {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_1", "name": "Work", "type": "user"},
        ]
    }

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_list_labels()

    env = json.loads(result)
    assert env["success"] is True
    assert len(env["data"]) == 2
    assert env["data"][0]["id"] == "INBOX"
    assert env["data"][1]["type"] == "user"


# ---------------------------------------------------------------------------
# gmail_delete_label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_delete_label_success():
    service = MagicMock()
    service.users().labels().delete().execute.return_value = None  # 204 no body

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_delete_label(label_id="Label_47")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["deleted_label_id"] == "Label_47"
    assert env["pagination"] is None


@pytest.mark.asyncio
async def test_gmail_delete_label_not_found_returns_error():
    from googleapiclient.errors import HttpError
    from unittest.mock import MagicMock as MM
    import json as _json

    resp = MM()
    resp.status = 404
    resp.reason = "Not Found"
    err = HttpError(resp=resp, content=_json.dumps({"error": {"message": "Label not found", "code": 404}}).encode())

    service = MagicMock()
    service.users().labels().delete().execute.side_effect = err

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_delete_label(label_id="Label_999")

    env = json.loads(result)
    assert env["success"] is False
    assert env["error"] is not None


# ---------------------------------------------------------------------------
# gmail_delete_filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_delete_filter_success():
    service = MagicMock()
    service.users().settings().filters().delete().execute.return_value = None  # 204 no body

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_delete_filter(filter_id="ANe1BmjHY47vZqni")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["deleted_filter_id"] == "ANe1BmjHY47vZqni"
    assert env["pagination"] is None


@pytest.mark.asyncio
async def test_gmail_delete_filter_not_found_returns_error():
    from googleapiclient.errors import HttpError
    from unittest.mock import MagicMock as MM
    import json as _json

    resp = MM()
    resp.status = 404
    resp.reason = "Not Found"
    err = HttpError(resp=resp, content=_json.dumps({"error": {"message": "Filter not found", "code": 404}}).encode())

    service = MagicMock()
    service.users().settings().filters().delete().execute.side_effect = err

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_delete_filter(filter_id="nonexistent")

    env = json.loads(result)
    assert env["success"] is False
    assert env["error"] is not None


# ---------------------------------------------------------------------------
# gmail_modify_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_modify_message_mark_read():
    service = MagicMock()
    service.users().messages().modify().execute.return_value = {
        "id": "msg1",
        "labelIds": ["INBOX"],
    }

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_modify_message(
            message_id="msg1",
            remove_labels=["UNREAD"],
        )

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["message_id"] == "msg1"
    assert "UNREAD" not in env["data"]["labels"]


@pytest.mark.asyncio
async def test_gmail_modify_message_no_labels_raises():
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError):
        await gmail_module.gmail_modify_message(message_id="msg1")


# ---------------------------------------------------------------------------
# gmail_send_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_send_message_basic():
    service = MagicMock()
    service.users().messages().send().execute.return_value = {
        "id": "sent1",
        "threadId": "thread1",
    }

    with patch.object(gmail_module, "_gmail_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await gmail_module.gmail_send_message(
            to=["recipient@example.com"],
            subject="Hello",
            body="This is a test.",
        )

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["message_id"] == "sent1"


# ---------------------------------------------------------------------------
# Decode helpers
# ---------------------------------------------------------------------------


def test_decode_body_roundtrip():
    original = "Hello, world! This is a test string with unicode: café"
    encoded = base64.urlsafe_b64encode(original.encode()).decode()
    # Remove padding to simulate Gmail's format
    encoded = encoded.rstrip("=")
    result = gmail_module._decode_body(encoded).decode("utf-8")
    assert result == original


def test_extract_body_plain_text():
    text = "Plain text body"
    encoded = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    payload = {
        "mimeType": "text/plain",
        "body": {"data": encoded},
        "parts": [],
    }
    plain, html = gmail_module._extract_body(payload)
    assert plain == text
    assert html is None


def test_extract_body_multipart():
    plain_text = "Plain version"
    html_text = "<p>HTML version</p>"
    enc_plain = base64.urlsafe_b64encode(plain_text.encode()).decode().rstrip("=")
    enc_html = base64.urlsafe_b64encode(html_text.encode()).decode().rstrip("=")
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/plain", "body": {"data": enc_plain}, "parts": []},
            {"mimeType": "text/html", "body": {"data": enc_html}, "parts": []},
        ],
    }
    plain, html = gmail_module._extract_body(payload)
    assert plain == plain_text
    assert html == html_text


def test_extract_attachments():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "text/plain",
                "filename": "",
                "body": {"data": "SGVsbG8="},
                "parts": [],
            },
            {
                "mimeType": "application/pdf",
                "filename": "report.pdf",
                "partId": "1",
                "body": {"attachmentId": "att_abc", "size": 204800},
                "parts": [],
            },
        ],
    }
    attachments = gmail_module._extract_attachments(payload)
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "report.pdf"
    assert attachments[0]["attachment_id"] == "att_abc"
    assert attachments[0]["size_bytes"] == 204800
