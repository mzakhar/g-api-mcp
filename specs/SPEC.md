# g-api-mcp — Google APIs MCP Server

**Phase:** Design (pre-implementation)
**Status:** Research complete, ready to scaffold

## What is this

A local stdio MCP server written in Python that wraps Gmail, Google Calendar, and Google Tasks behind a consistent tool interface designed for LLM agents. Primary consumers: Claude Code and Claude Desktop.

Design goals:
- Every response is wrapped in a metadata envelope so the LLM can reason about payload size before consuming it
- All list tools return thin summaries with bounded result counts; full content is fetched via explicit `get_*` calls
- Pagination is cursor-based and always surfaced — no silent auto-pagination that hides data volume
- Auth is a one-time setup step, completely separate from the server process

---

## Status

- [x] Research complete
- [x] Project scaffold (pyproject.toml, package layout)
- [x] Auth setup script
- [x] Response envelope utility
- [x] Gmail tools
- [x] Calendar tools
- [x] Tasks tools
- [x] .mcp.json Claude Code registration
- [x] README / setup guide
- [x] Tests (envelope unit tests + tool handler tests with mocked Google services)
- [x] `.gitattributes` (line ending normalization)

---

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| MCP framework | `mcp[cli]` ≥ 1.26.0 (FastMCP) | High-level decorator API; auto-generates inputSchema from type annotations |
| Google APIs | `google-api-python-client` ≥ 2.100.0 | Official client; handles discovery, serialization, HTTP retries |
| Auth | `google-auth-oauthlib` ≥ 1.1.0 | InstalledAppFlow for desktop OAuth2 |
| Transport | `google-auth-httplib2` | httplib2 backend for the google client |
| Token storage | `keyring` (Windows Credential Locker) with plaintext `token.json` fallback | Refresh token encrypted at rest via DPAPI on Windows |
| Python | ≥ 3.11 | Required for `X | Y` union syntax and `asyncio.to_thread` |

---

## Project Structure

```
g-api-mcp/
├── specs/
│   └── SPEC.md                  ← this file
├── pyproject.toml
├── .mcp.json                    ← Claude Code registration (project-scoped)
├── auth_setup.py                ← one-time OAuth2 flow; run before starting server
└── src/
    └── g_api_mcp/
        ├── __init__.py
        ├── server.py            ← FastMCP entry point; mcp.run() here
        ├── auth.py              ← GoogleCredentialManager; token load/refresh
        ├── envelope.py          ← build_envelope(), estimate_tokens()
        ├── gmail.py             ← Gmail tool implementations
        ├── calendar.py          ← Calendar tool implementations
        └── tasks.py             ← Tasks tool implementations
```

---

## OAuth2 Auth Design

### One-time setup (separate from server)

Run `python auth_setup.py` once before starting the server. This opens a browser, completes the OAuth2 consent flow, and saves credentials to disk. The server never opens a browser — it only loads and refreshes tokens.

```python
# auth_setup.py
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path
import json, keyring

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
creds = flow.run_local_server(port=0)  # OS-assigned port, avoids conflicts

# Store in Windows Credential Locker via keyring
keyring.set_password("g-api-mcp", "oauth_token", creds.to_json())
print("Auth complete. You can now start the MCP server.")
```

**Why `port=0`:** lets the OS assign a free port; avoids conflicts if the port is already in use.

**Why `keyring`:** refresh tokens are sensitive. Windows Credential Locker (DPAPI) encrypts them at rest, decryptable only by the same Windows user session. Fallback: plain `~/.config/g-api-mcp/token.json` guarded by filesystem ACLs.

**Token expiry:**
- Access tokens: 1 hour (auto-refreshed by `GoogleCredentialManager`)
- Refresh tokens: 6 months inactivity, 7 days if OAuth consent screen is in Testing mode
- Re-run `auth_setup.py` if the server surfaces an auth error

### Credential manager (used by server at runtime)

```python
# src/g_api_mcp/auth.py
import threading, keyring
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

class GoogleCredentialManager:
    def __init__(self):
        self._creds: Credentials | None = None
        self._lock = threading.Lock()

    def get_valid_credentials(self) -> Credentials:
        """Return valid credentials, refreshing if necessary. Thread-safe."""
        with self._lock:
            if self._creds is None:
                raw = keyring.get_password("g-api-mcp", "oauth_token")
                if not raw:
                    raise RuntimeError(
                        "No Google credentials found. Run: python auth_setup.py"
                    )
                self._creds = Credentials.from_authorized_user_info(
                    json.loads(raw), SCOPES
                )

            if not self._creds.valid:
                if self._creds.expired and self._creds.refresh_token:
                    try:
                        self._creds.refresh(Request())
                        # Refresh token may rotate — persist the new value
                        keyring.set_password("g-api-mcp", "oauth_token", self._creds.to_json())
                    except RefreshError as e:
                        raise RuntimeError(
                            f"Google refresh token expired: {e}. "
                            "Run: python auth_setup.py"
                        ) from e
                else:
                    raise RuntimeError(
                        "Invalid credentials. Run: python auth_setup.py"
                    )

            return self._creds
```

`creds.refresh()` is synchronous/blocking. In async tool handlers, wrap it:
```python
import asyncio
creds = await asyncio.to_thread(cred_manager.get_valid_credentials)
```

### Google Cloud project setup

1. `console.cloud.google.com` → create/select project
2. **APIs & Services → Library**: enable Gmail API, Google Calendar API, Tasks API
3. **APIs & Services → OAuth consent screen**: set up (External or Internal), add yourself as test user
4. **APIs & Services → Credentials → Create → OAuth client ID → Desktop app** → download as `client_secrets.json`
5. Place `client_secrets.json` in project root
6. Run `python auth_setup.py`

**Important:** If consent screen is in "Testing" mode with External user type, refresh tokens expire after **7 days**. For a personal permanent tool, either switch to Internal (requires Google Workspace) or accept periodic re-auth.

---

## OAuth2 Scopes

| Scope | Why needed |
|---|---|
| `gmail.modify` | Read inbox, search, mark read/unread, apply labels |
| `gmail.send` | Send messages and manage drafts |
| `calendar` | Full calendar access (read + write events + calendar list) |
| `tasks` | Full tasks access (read + write) |

All four scopes are requested in a single auth flow and issued in a single access token. To make the server read-only, swap to `gmail.readonly`, `calendar.readonly`, `tasks.readonly` — these still require Google verification for External published apps but work fine in Testing mode.

---

## Response Envelope Schema

Every tool response is wrapped in this envelope. This is the core context-management mechanism.

### TypeScript interface (canonical definition)

```typescript
interface Pagination {
  has_more: boolean;
  next_cursor: string | null;   // opaque pageToken from upstream API
  result_count: number;         // items in THIS response (not total)
  total_estimate: number | null; // null when API cannot provide it
}

interface ContextHint {
  estimated_tokens: number;     // cheap heuristic: len(json.dumps(data)) // 4
  warning: string | null;       // non-null when payload is large
}

interface McpEnvelope<T = unknown> {
  success: boolean;
  data: T | null;               // null on error
  pagination: Pagination | null; // null for singleton (get_*) responses
  context_hint: ContextHint;
  error: string | null;         // null on success
}
```

### Token warning thresholds

| Threshold | Action |
|---|---|
| < 2,000 tokens | No warning |
| 2,000–8,000 tokens | Soft warning: "moderate payload — use get_* for specific IDs if needed" |
| > 8,000 tokens | Hard warning: "large payload — consider fetching specific items by ID" |

### Python implementation

```python
# src/g_api_mcp/envelope.py
from __future__ import annotations
import json
from typing import Any

WARN_MODERATE = 2_000
WARN_LARGE    = 8_000


def estimate_tokens(obj: Any) -> int:
    """Fast ~4 chars/token heuristic. Accurate to ±25% for English/JSON text."""
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def build_envelope(
    *,
    data: Any = None,
    has_more: bool = False,
    next_cursor: str | None = None,
    result_count: int | None = None,
    total_estimate: int | None = None,
    is_list: bool = True,
    error: str | None = None,
) -> dict:
    success = error is None
    tokens = estimate_tokens(data)

    warning = None
    if tokens > WARN_LARGE:
        warning = (
            f"large payload (~{tokens:,} tokens) — consider fetching "
            "specific items by ID rather than consuming the full response"
        )
    elif tokens > WARN_MODERATE:
        warning = (
            f"moderate payload (~{tokens:,} tokens) — use get_* tools "
            "for specific IDs if you need only a subset"
        )

    pagination = None
    if is_list:
        count = result_count if result_count is not None else (
            len(data) if isinstance(data, list) else 0
        )
        pagination = {
            "has_more": has_more,
            "next_cursor": next_cursor,
            "result_count": count,
            "total_estimate": total_estimate,
        }

    return {
        "success": success,
        "data": data if success else None,
        "pagination": pagination,
        "context_hint": {"estimated_tokens": tokens, "warning": warning},
        "error": error,
    }
```

### Example: list response

```json
{
  "success": true,
  "data": [
    {
      "id": "18f3a2c9d1b4e7f0",
      "thread_id": "18f3a2c9d1b4e7f0",
      "subject": "Q2 budget review — action required",
      "from": "finance@example.com",
      "date": "2026-04-04T14:23:00Z",
      "snippet": "Please review the attached figures before Friday...",
      "labels": ["INBOX", "IMPORTANT"],
      "has_attachments": true
    }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "CAESFBoSCgwIAhCVkcWEB...",
    "result_count": 20,
    "total_estimate": 847
  },
  "context_hint": {
    "estimated_tokens": 680,
    "warning": null
  },
  "error": null
}
```

### Example: large singleton (triggers warning)

```json
{
  "success": true,
  "data": { "id": "...", "body_text": "...long email body..." },
  "pagination": null,
  "context_hint": {
    "estimated_tokens": 9420,
    "warning": "large payload (~9,420 tokens) — consider fetching specific items by ID rather than consuming the full response"
  },
  "error": null
}
```

### Example: error

```json
{
  "success": false,
  "data": null,
  "pagination": null,
  "context_hint": { "estimated_tokens": 18, "warning": null },
  "error": "Google API error 403: insufficient permission for gmail.modify scope"
}
```

---

## Tool Taxonomy

### Design principles

1. **List tools return thin summaries only** — IDs, subject/title, dates, small metadata. No body content.
2. **Get tools return full content for one ID** — only call after deciding you need it.
3. **All list tools accept `page_cursor`** — pass `pagination.next_cursor` from a previous response.
4. **`max_results` is bounded per tool** — server enforces a hard cap to prevent accidental huge fetches.
5. **Defaults are conservative** — `max_results` defaults to 20–25, not 100 or 500.

---

### Gmail Tools (7 tools)

#### `gmail_list_messages`

Returns thin message summaries. Never returns body content.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | `""` | Gmail search syntax: `from:`, `subject:`, `is:unread`, `after:2025/01/01`, `has:attachment`, etc. |
| `max_results` | `int` | `20` | 1–100 (server hard cap) |
| `page_cursor` | `str \| None` | `null` | Pass `pagination.next_cursor` from a prior call |
| `label_ids` | `list[str]` | `[]` | Filter by label IDs (e.g., `["INBOX"]`) |

Returns: `list[ThinMessage]` where each item has:
`id`, `thread_id`, `subject`, `from`, `date`, `snippet` (100–200 chars), `labels`, `has_attachments`

Envelope has pagination. Token cost: ~20–40 tokens per message.

---

#### `gmail_get_message`

Fetches full content for one message.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `message_id` | `str` | required | Gmail message ID from a list response |
| `include_html` | `bool` | `false` | Also return `body_html` (adds tokens; text is usually sufficient) |

Returns: `FullMessage` — `id`, `thread_id`, `subject`, `from`, `to`, `cc`, `date`, `body_text`, `body_html?`, `attachments[]` (filename, mime_type, size_bytes — not the binary data)

Envelope has no pagination. Token cost: 500–15,000+ depending on email length.

---

#### `gmail_send_message`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `to` | `list[str]` | required | Recipient email addresses |
| `subject` | `str` | required | Subject line |
| `body` | `str` | required | Plain-text body |
| `cc` | `list[str]` | `[]` | CC recipients |
| `bcc` | `list[str]` | `[]` | BCC recipients |
| `reply_to_message_id` | `str \| None` | `null` | If set, threads the reply via `In-Reply-To` + `References` headers and `threadId` |

Returns: `{ "message_id": str, "thread_id": str }`

---

#### `gmail_create_draft`

Same parameters as `gmail_send_message` (minus `reply_to_message_id`). Saves to Drafts only.

Returns: `{ "draft_id": str, "message_id": str }`

---

#### `gmail_list_labels`

No parameters. Returns all labels (system + user-created).

Returns: `list[{ "id": str, "name": str, "type": "system" | "user" }]`

---

#### `gmail_modify_message`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `message_id` | `str` | required | Target message |
| `add_labels` | `list[str]` | `[]` | Label IDs to apply (e.g., `"STARRED"`, `"UNREAD"`) |
| `remove_labels` | `list[str]` | `[]` | Label IDs to remove (e.g., `"UNREAD"` to mark read) |

Returns: `{ "message_id": str, "labels": list[str] }` — post-modification label state

---

#### `gmail_get_attachment`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `message_id` | `str` | required | Parent message ID |
| `attachment_id` | `str` | required | `attachmentId` from a `gmail_get_message` response |
| `filename` | `str` | required | Filename for saving |
| `save_path` | `str` | required | Local filesystem path to write the file |

Returns: `{ "saved_to": str, "size_bytes": int }`

---

### Calendar Tools (7 tools)

#### `calendar_list_calendars`

No parameters. Returns all calendars the user has access to.

Returns: `list[{ "id": str, "summary": str, "primary": bool, "access_role": str, "time_zone": str }]`

---

#### `calendar_list_events`

Returns thin event summaries.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calendar_id` | `str` | `"primary"` | Calendar ID; use `"primary"` for default |
| `time_min` | `str` | now | RFC 3339 lower bound (inclusive) |
| `time_max` | `str` | now+7d | RFC 3339 upper bound (exclusive) |
| `query` | `str \| None` | `null` | Free-text search across title, description, location, attendees |
| `max_results` | `int` | `25` | 1–250 |
| `page_cursor` | `str \| None` | `null` | From `pagination.next_cursor` |

Returns: `list[ThinEvent]` — `id`, `summary`, `start`, `end`, `status`, `organizer_email`, `attendee_count`

Token cost: ~30–50 tokens per event.

---

#### `calendar_get_event`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calendar_id` | `str` | `"primary"` | Calendar ID |
| `event_id` | `str` | required | Event ID from a list response |

Returns: `FullEvent` — adds `description`, full `attendees[]` (with `responseStatus`), `location`, `conference_link`, `recurrence[]`, `transparency`

---

#### `calendar_create_event`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calendar_id` | `str` | `"primary"` | Calendar ID |
| `summary` | `str` | required | Event title |
| `start` | `str` | required | RFC 3339 datetime |
| `end` | `str` | required | RFC 3339 datetime |
| `description` | `str \| None` | `null` | Event notes/body |
| `attendees` | `list[str]` | `[]` | Email addresses to invite |
| `location` | `str \| None` | `null` | Location string |
| `send_notifications` | `bool` | `true` | Email invites to attendees |

Returns: `{ "event_id": str, "html_link": str, "summary": str, "start": str }`

---

#### `calendar_update_event`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calendar_id` | `str` | `"primary"` | Calendar ID |
| `event_id` | `str` | required | Target event ID |
| `summary` | `str \| None` | `null` | New title (omit to keep current) |
| `start` | `str \| None` | `null` | New start time |
| `end` | `str \| None` | `null` | New end time |
| `description` | `str \| None` | `null` | New description |
| `location` | `str \| None` | `null` | New location |
| `send_notifications` | `bool` | `true` | Notify attendees of change |

Returns: `{ "event_id": str, "updated": str }`

---

#### `calendar_delete_event`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calendar_id` | `str` | `"primary"` | Calendar ID |
| `event_id` | `str` | required | Target event ID |
| `send_notifications` | `bool` | `true` | Send cancellation to attendees |

Returns: `{ "deleted": true }`

---

#### `calendar_quick_add`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calendar_id` | `str` | `"primary"` | Calendar ID |
| `text` | `str` | required | Natural-language string, e.g. `"Lunch with Alex tomorrow at noon"` |

Returns: `{ "event_id": str, "summary": str, "start": str, "end": str }` — always show the parsed time back to the user for confirmation.

---

### Tasks Tools (7 tools)

#### `tasks_list_tasklists`

No parameters. Returns all task lists.

Returns: `list[{ "id": str, "title": str, "updated": str }]`

---

#### `tasks_list_tasks`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasklist_id` | `str` | `"@default"` | Task list ID (use `tasks_list_tasklists` to enumerate) |
| `show_completed` | `bool` | `false` | Include completed tasks |
| `due_min` | `str \| None` | `null` | RFC 3339 — only tasks due on or after |
| `due_max` | `str \| None` | `null` | RFC 3339 — only tasks due before |
| `max_results` | `int` | `20` | 1–100 |
| `page_cursor` | `str \| None` | `null` | From `pagination.next_cursor` |

Returns: `list[ThinTask]` — `id`, `title`, `status`, `due`, `updated`, `has_notes`, `has_parent`

Token cost: ~15–25 tokens per task.

---

#### `tasks_get_task`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasklist_id` | `str` | `"@default"` | Task list ID |
| `task_id` | `str` | required | Task ID |

Returns: `FullTask` — adds `notes`, `links[]`, `parent` (subtask parent ID), `position`

---

#### `tasks_create_task`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasklist_id` | `str` | `"@default"` | Task list ID |
| `title` | `str` | required | Task title |
| `notes` | `str \| None` | `null` | Task notes/description |
| `due` | `str \| None` | `null` | RFC 3339 date (time component ignored by API) |
| `parent_task_id` | `str \| None` | `null` | Creates as a subtask of this task |

Returns: `{ "task_id": str, "title": str, "status": "needsAction" }`

---

#### `tasks_update_task`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasklist_id` | `str` | `"@default"` | Task list ID |
| `task_id` | `str` | required | Target task ID |
| `title` | `str \| None` | `null` | New title |
| `notes` | `str \| None` | `null` | New notes |
| `due` | `str \| None` | `null` | New due date |
| `status` | `str \| None` | `null` | `"needsAction"` or `"completed"` |

Returns: `{ "task_id": str, "updated": str }`

---

#### `tasks_complete_task`

Convenience wrapper. Sets `status="completed"` and stamps `completed` datetime.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasklist_id` | `str` | `"@default"` | Task list ID |
| `task_id` | `str` | required | Target task ID |

Returns: `{ "task_id": str, "status": "completed", "completed": str }`

---

#### `tasks_delete_task`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasklist_id` | `str` | `"@default"` | Task list ID |
| `task_id` | `str` | required | Target task ID |

Returns: `{ "deleted": true }`

---

## API Pagination Details

### Gmail — `messages.list`

```
max maxResults: 500  (server will cap at 100 in MCP tools)
pageToken       → pass as page_cursor
nextPageToken   → returned as pagination.next_cursor
messages []     → may be absent (not empty) when query matches nothing
```

**Important:** `messages.list` returns only `{id, threadId}`. Full content requires a separate `messages.get` call per message. Use batch requests (up to 100 per batch) to amortize HTTP overhead.

### Google Calendar — `events.list`

```
max maxResults: 2500  (server will cap at 250 in MCP tools)
pageToken       → pass as page_cursor
nextPageToken   → returned as pagination.next_cursor (absent on last page)
nextSyncToken   → present on last page of a full sync; use for delta sync
```

**Delta sync via syncToken:** Store `nextSyncToken` after a full sync. Pass it in subsequent `events.list` calls to get only changed/deleted events since the last sync. Sync tokens expire after ~7 days; a `410 Gone` response signals you must do a full sync again.

### Google Tasks — `tasks.list`

```
max maxResults: 100  (same as server cap)
pageToken       → pass as page_cursor
nextPageToken   → returned as pagination.next_cursor
```

No syncToken equivalent. Use `updatedMin` (RFC 3339) for incremental polling.

---

## Gmail API Key Facts

### Message formats (performance guide)

| Format | Returns | Approx size | Use when |
|---|---|---|---|
| `MINIMAL` | id, threadId, labelIds, snippet, internalDate | 200–400 bytes | Sorting/filtering only |
| `METADATA` | MINIMAL + headers | 1–3 KB | Subject/From/Date without body |
| `FULL` | METADATA + MIME tree + body parts (base64url) | 5–50 KB | Reading message content |
| `RAW` | Entire RFC 2822 as base64url string | Full message size | Re-encoding for forward/MIME parsing |

List tools use `METADATA` with selected headers. Get tools use `FULL`.

### Base64url decoding

```python
import base64

def decode_body(data: str) -> bytes:
    padded = data + '=' * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(padded)
```

Gmail encodes all body `data` fields with base64url (RFC 4648 §5). Python's `base64.urlsafe_b64decode` requires padding to be added back manually.

### MIME tree walking

The message payload is a recursive tree. Walk `parts[]` recursively to find `text/plain`, `text/html`, and attachment parts. When `mimeType` starts with `multipart/`, data is in `parts[]` not `body.data`. Attachments > ~2 KB have `body.attachmentId` instead of `body.data` and must be fetched via `messages.attachments.get`.

### Quota (per user per day)

| Operation | Quota units |
|---|---|
| messages.list | 5 |
| messages.get | 5 |
| messages.send | 100 |
| messages.modify | 5 |
| drafts.create | 10 |
| labels.list | 1 |
| Batch | Sum of contained calls |

Total: 1B units/day per project. Per-user: 250 units/second.

### Sending replies

Include `threadId` in the send body, and set `In-Reply-To` and `References` headers in the RFC 2822 message to ensure correct threading.

---

## Calendar API Key Facts

### Event fields reference

| Field | Notes |
|---|---|
| `start.dateTime` / `start.date` | `date` = all-day event (no time zone) |
| `attendees[].responseStatus` | `needsAction`, `accepted`, `declined`, `tentative` |
| `status` | `confirmed`, `tentative`, `cancelled` (deleted in delta sync) |
| `recurrence[]` | RRULE strings per RFC 5545 |
| `recurringEventId` | Links instance to master recurring event |
| `originalStartTime` | Original scheduled time for modified instances |
| `transparency` | `opaque` (busy) or `transparent` (free) |
| `conferenceData.entryPoints[].uri` | Google Meet link |

### Recurring events

- `singleEvents=false` (default): returns master recurring events
- `singleEvents=true`: expands each instance; required for `orderBy=startTime`
- Modified instances have `recurringEventId` + `originalStartTime` + their actual `start`/`end`
- Cancelled instances have `status="cancelled"` (only visible with `showDeleted=true`)

### Time zone

Always use RFC 3339 format with explicit offset for `timeMin`/`timeMax`. UTC (`Z` suffix) is safest for queries. Return `start.dateTime` and `start.date` as-is to the caller.

---

## Tasks API Key Facts

- `due` field: time portion is always ignored; treated as date-only
- `status`: `"needsAction"` or `"completed"`. Set `completed` timestamp when marking done.
- `position`: zero-padded sort string. Do not construct manually; use `tasks.move` to reorder.
- `parent`: task ID of parent for subtasks (absent for top-level tasks)
- No syncToken; use `updatedMin` for incremental polling
- `tasks.clear`: permanently deletes all completed tasks — irreversible; use with confirmation

---

## FastMCP Server Entry Point

```python
# src/g_api_mcp/server.py
import sys, logging
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stderr,   # stdout is the MCP wire — never log to stdout
)

mcp = FastMCP(
    "g-api-mcp",
    instructions=(
        "Google APIs MCP server. Tools return JSON envelopes with pagination "
        "and context_hint.estimated_tokens. Use list_* tools first to get IDs "
        "and summaries; use get_* tools only for items you need to read in full. "
        "Check pagination.has_more and pass pagination.next_cursor as page_cursor "
        "to continue fetching. If context_hint.warning is non-null, reconsider "
        "whether you need to fetch more data before proceeding."
    ),
)

# Import tool modules (they register tools via @mcp.tool() decorators)
from g_api_mcp import gmail, calendar, tasks  # noqa: F401 E402

def main():
    mcp.run()

if __name__ == "__main__":
    main()
```

**Critical:** All logging must go to `stderr`. `stdout` is the JSON-RPC wire for the MCP protocol.

### Tool handler pattern

```python
# src/g_api_mcp/gmail.py
import json, asyncio
from mcp.server.fastmcp.exceptions import ToolError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from g_api_mcp.server import mcp
from g_api_mcp.auth import cred_manager
from g_api_mcp.envelope import build_envelope


async def get_gmail_service():
    creds = await asyncio.to_thread(cred_manager.get_valid_credentials)
    return build("gmail", "v1", credentials=creds)


@mcp.tool()
async def gmail_list_messages(
    query: str = "",
    max_results: int = 20,
    page_cursor: str | None = None,
    label_ids: list[str] | None = None,
) -> str:
    """List Gmail messages matching a query. Returns thin summaries only (no body).
    Use gmail_get_message to read full content for specific message IDs.
    Pass pagination.next_cursor as page_cursor to fetch the next page."""
    max_results = max(1, min(max_results, 100))  # enforce bounds
    try:
        service = await get_gmail_service()
        kwargs = {"userId": "me", "maxResults": max_results, "q": query}
        if page_cursor:
            kwargs["pageToken"] = page_cursor
        if label_ids:
            kwargs["labelIds"] = label_ids

        result = await asyncio.to_thread(
            lambda: service.users().messages().list(**kwargs).execute()
        )
    except HttpError as e:
        return json.dumps(build_envelope(error=f"Google API error {e.status_code}: {e.reason}"))

    message_stubs = result.get("messages", [])
    has_more = "nextPageToken" in result
    total_estimate = result.get("resultSizeEstimate")

    # Batch-fetch METADATA for each stub
    thin_messages = []
    if message_stubs:
        batch_results = {}
        batch = service.new_batch_http_request()

        def make_callback(msg_id):
            def cb(req_id, resp, err):
                batch_results[msg_id] = resp if not err else None
            return cb

        for stub in message_stubs:
            batch.add(
                service.users().messages().get(
                    userId="me", id=stub["id"], format="METADATA",
                    metadataHeaders=["From", "Subject", "Date"],
                    fields="id,threadId,snippet,internalDate,labelIds,payload/headers,payload/parts"
                ),
                callback=make_callback(stub["id"])
            )

        await asyncio.to_thread(batch.execute)

        for stub in message_stubs:
            raw = batch_results.get(stub["id"])
            if raw:
                thin_messages.append(_to_thin_message(raw))

    envelope = build_envelope(
        data=thin_messages,
        has_more=has_more,
        next_cursor=result.get("nextPageToken"),
        total_estimate=total_estimate,
    )
    return json.dumps(envelope)


def _to_thin_message(raw: dict) -> dict:
    headers = {h["name"]: h["value"] for h in raw.get("payload", {}).get("headers", [])}
    has_attachments = any(
        p.get("filename") for p in raw.get("payload", {}).get("parts", [])
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
    }
```

---

## Claude Code Registration

### `.mcp.json` (project-scoped — place in repo root)

```json
{
  "mcpServers": {
    "g-api-mcp": {
      "command": "python",
      "args": ["-m", "g_api_mcp.server"],
      "cwd": "C:\\EpicSource\\Projects\\g-api-mcp",
      "env": {}
    }
  }
}
```

Or with an explicit Python path (avoids PATH ambiguity):

```json
{
  "mcpServers": {
    "g-api-mcp": {
      "command": "C:\\Users\\mzakhar\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
      "args": ["-m", "g_api_mcp.server"],
      "cwd": "C:\\EpicSource\\Projects\\g-api-mcp",
      "env": {}
    }
  }
}
```

For global registration (available in all projects), add the same `mcpServers` block to `~/.claude/settings.json`.

---

## Agentic Patterns and Usage Guide

These patterns describe how Claude should use this server effectively.

### Pattern 1: Email search and selective read

```
Step 1  gmail_list_messages(query="budget Q2", max_results=20)
        → 12 thin items returned, estimated_tokens=680, no warning
        → read subjects + snippets to identify relevant messages

Step 2  gmail_get_message("id_a")  →  ~1,800 tokens
        gmail_get_message("id_b")  →  ~4,100 tokens (warning fires)

Total: ~7,500 tokens  vs  ~36,000 if all 12 bodies fetched upfront
```

**Rule:** If `context_hint.warning` fires on a `get_message`, decide whether you truly need the full body or can answer from the snippet already in context.

### Pattern 2: Calendar availability check

```
Step 1  calendar_list_events(
            time_min="2026-04-06T00:00:00Z",
            time_max="2026-04-11T23:59:59Z",
            max_results=100
        )
        → 18 thin events (start + end only = sufficient for slot finding)
        → estimated_tokens=890

Step 2  Agent builds free/busy grid from start+end fields alone.
        No calendar_get_event needed unless you need to reason about content.

Step 3  If "can this meeting move?": calendar_get_event(event_id="xyz")
        → check attendees, recurrence, organizer
```

The thin list contains `start` and `end` — that's all you need for free/busy analysis.

### Pattern 3: Task triage

```
Step 1  tasks_list_tasklists() → ["Work", "Personal"]
Step 2  tasks_list_tasks(tasklist_id="work_id", due_max="2026-04-05T23:59:59Z")
        → 23 overdue tasks, title+due+status, estimated_tokens=640
Step 3  Classify by title: urgent / reschedule / delete
        (titles sufficient — no tasks_get_task needed for most)
Step 4  For ambiguous tasks: tasks_get_task("id") → read notes
Step 5  Batch updates: tasks_update_task × N, tasks_complete_task × M
```

### Pattern 4: Cross-service coordination

```
Step 1  gmail_list_messages(query="subject:kickoff project")
        → identify message from snippet
Step 2  gmail_get_message("id_x") → extract meeting date
Step 3  calendar_list_events(time_min="...", time_max="...") → confirm event exists
Step 4  tasks_create_task(title="Follow up after kickoff", due="...")
```

The uniform envelope schema across all three APIs means the same reasoning pattern applies throughout.

### Pagination decision rule

Before fetching the next page:
1. Check `pagination.has_more` — if false, you have everything
2. Check `context_hint.estimated_tokens` for what you already have
3. Ask: do I need more, or can I answer from what's in context?
4. If fetching more: pass `pagination.next_cursor` as `page_cursor`
5. Never loop more than 20 pages without a stopping condition

### Error handling

| Error signal | Agent action |
|---|---|
| `401`, `invalid_grant`, `insufficient permission` | Stop, surface auth error, tell user to run `auth_setup.py` |
| `429`, `rateLimitExceeded` | Back off and retry (up to 3 times) |
| `403 dailyLimitExceeded` | Stop, report quota exhaustion |
| `404` | Do not retry — ID is wrong or item was deleted |
| `410 Gone` (Calendar) | Sync token expired; tell user to use `calendar_list_events` without `syncToken` |

---

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "g-api-mcp"
version = "0.1.0"
description = "Google APIs (Gmail, Calendar, Tasks) MCP server"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.26.0",
    "google-api-python-client>=2.100.0",
    "google-auth>=2.23.0",
    "google-auth-oauthlib>=1.1.0",
    "google-auth-httplib2>=0.1.1",
    "keyring>=24.0.0",
]

[project.scripts]
g-api-mcp = "g_api_mcp.server:main"
g-api-mcp-auth = "auth_setup:main"

[tool.hatch.build.targets.wheel]
packages = ["src/g_api_mcp"]
```

---

## Open Questions / Implementation Notes

1. **`mcp` tool registration with shared `mcp` instance across modules** — the `server.py` creates the `FastMCP` instance; Gmail/Calendar/Tasks modules import it and use `@mcp.tool()`. Circular import risk: modules import from `server.py`, which imports the modules. Resolve with a `get_mcp()` factory or a separate `instance.py` that holds only the `FastMCP` object.

2. **`google-api-python-client` discovery cache** — first run does a network request to fetch the API discovery document. Cache in `~/.cache/google-api-python-client/` (default). Can pass `cache_discovery=False` to disable caching if startup time matters.

3. **Async vs sync google-api-python-client** — the official client is synchronous. All Google API calls must be wrapped in `asyncio.to_thread()` to avoid blocking the FastMCP event loop.

4. **Batch requests in async context** — `batch.execute()` is synchronous; wrap with `asyncio.to_thread(batch.execute)`.

5. **`tasks.clear` confirmation** — permanently deletes all completed tasks. Consider requiring a `confirm=true` parameter or surfacing a warning in the tool description. Do not call silently.

6. **HTML body handling** — raw HTML bodies can be very large and contain noise (inline CSS, tracking pixels). Consider stripping HTML to text via `html.parser` or `bleach` before returning. The `include_html` flag in `gmail_get_message` handles the deliberate case.
