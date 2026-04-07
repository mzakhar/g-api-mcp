# g-api-mcp

A local stdio MCP server wrapping Gmail, Google Calendar, Google Tasks, and Google Contacts for use with Claude Code and Claude Desktop.

## Features

- **34 tools** across Gmail (13), Calendar (7), Tasks (8), and Contacts (6)
- **Context-efficient responses** — every tool returns a JSON envelope with `pagination` and `context_hint.estimated_tokens` so the LLM can plan before fetching more data
- **List-then-fetch pattern** — list tools return thin summaries (IDs + metadata, no body); get tools fetch full content for specific IDs on demand
- **Cursor-based pagination** — all list tools accept `page_cursor` and return `pagination.next_cursor`
- **Secure credential storage** — refresh token stored in Windows Credential Locker via `keyring`; client identity read fresh from `client_secrets.json` on every refresh (supports client secret rotation without re-auth)

## Tools

### Gmail (13)

| Tool | Description |
|---|---|
| `gmail_list_messages` | Search/list messages — returns thin summaries only |
| `gmail_get_message` | Full content (body, headers, attachment metadata, unsubscribe headers) for one message |
| `gmail_send_message` | Compose and send; supports threaded replies |
| `gmail_create_draft` | Save to Drafts without sending |
| `gmail_modify_message` | Add/remove labels on one message (mark read, star, archive, etc.) |
| `gmail_bulk_modify` | Add/remove labels on up to 1000 messages in one call |
| `gmail_get_attachment` | Download an attachment to a local path |
| `gmail_list_labels` | All labels (system + user-created) with IDs |
| `gmail_create_label` | Create a new label; use `/` for nesting (e.g. `Finance/Receipts`) |
| `gmail_delete_label` | Delete a label (messages are not deleted) |
| `gmail_list_filters` | All inbox filters with criteria and actions |
| `gmail_create_filter` | Create a filter to auto-label or archive incoming mail |
| `gmail_delete_filter` | Delete a filter by ID |

`gmail_get_message` returns three unsubscribe fields:

| Field | Description |
|---|---|
| `list_unsubscribe` | Raw `List-Unsubscribe` header value (mailto: or https: URL) |
| `list_unsubscribe_post` | Raw `List-Unsubscribe-Post` header value |
| `one_click_unsubscribe` | `true` if sender supports RFC 8058 one-click unsubscribe |

### Calendar (7)

| Tool | Description |
|---|---|
| `calendar_list_calendars` | All calendars with IDs and access roles |
| `calendar_list_events` | Events in a time range — thin summaries |
| `calendar_get_event` | Full event (attendees, conference link, recurrence) |
| `calendar_create_event` | Create an event with optional attendees and Meet link |
| `calendar_update_event` | Partial update (PATCH) — only supplied fields change |
| `calendar_delete_event` | Delete with optional cancellation notices |
| `calendar_quick_add` | Natural-language event creation |

### Tasks (8)

| Tool | Description |
|---|---|
| `tasks_list_tasklists` | All task lists with IDs |
| `tasks_list_tasks` | Tasks in a list — thin summaries, filterable by due date |
| `tasks_get_task` | Full task (notes, links, parent/subtask info) |
| `tasks_create_task` | Create a task, optionally as a subtask |
| `tasks_update_task` | Partial update — title, notes, due date, status |
| `tasks_complete_task` | Mark done and stamp completion time |
| `tasks_delete_task` | Permanently delete a task |
| `tasks_sync_to_vault` | Sync tasks to an Obsidian vault (see [Sync](#sync) below) |

### Contacts (6)

| Tool | Description |
|---|---|
| `contacts_list` | All contacts — thin summaries |
| `contacts_get` | Full contact by resource name |
| `contacts_search` | Search by name, email, or phone |
| `contacts_create` | Create a new contact |
| `contacts_update` | Update fields on an existing contact |
| `contacts_delete` | Delete a contact |

### Sync

`tasks_sync_to_vault` fetches tasks changed since last sync, writes them to an Obsidian vault as formatted task lines, and marks them complete in Google Tasks.

Driven by a config file at `%APPDATA%\g-api-mcp\sync-config.json`. Copy `sync-config.example.json` from the project root and edit:

```json
{
  "vault_path": "C:\\Obsidian\\MyVault",
  "daily_notes_path": "00 Daily Plan",
  "daily_note_section": "## Top Priorities",
  "poll_interval_seconds": 60,
  "task_lists": [
    { "id": "<tasklist-id>", "name": "My List" },
    { "id": "<tasklist-id>", "name": "Work", "project_note": "20 Projects/Work/Work.md" }
  ]
}
```

**Routing rules:**
- Task lists with `project_note` → written to that note under `## Work Plan`
- Tasks with a due date → written to the daily note for that date under `daily_note_section`
- Tasks with no due date → written to today's daily note under `## Someday / Maybe`

**Sync flow:** write to vault → append `[synced-to-vault: ...]` to the task's Google notes field → mark complete in Google Tasks. Re-running is safe — already-processed tasks are skipped via a state file at `%APPDATA%\g-api-mcp\sync-state.json`.

Can also be run as a standalone CLI:

```bash
g-api-mcp-sync
```

## Response Envelope

Every tool returns a JSON string with this shape:

```json
{
  "success": true,
  "data": [...],
  "pagination": {
    "has_more": true,
    "next_cursor": "CAESFBoS...",
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

- `context_hint.estimated_tokens` — fast token estimate (`len(json.dumps(data)) // 4`). Read this before deciding to fetch more pages.
- `context_hint.warning` — non-null when payload exceeds ~2,000 tokens (soft) or ~8,000 tokens (hard). Consider fetching specific IDs with `get_*` tools instead.
- `pagination.next_cursor` — pass as `page_cursor` in the next call to page through results.
- Singleton responses (`get_*` tools) have `pagination: null`.

## Setup

### Prerequisites

- Python 3.11+
- Windows (credential storage uses Windows Credential Locker via `keyring`)
- A Google account

### 1. Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. **APIs & Services → Library** — search for and enable each of these:
   - **Gmail API**
   - **Google Calendar API**
   - **Tasks API**
   - **People API** (required for Contacts tools)
4. **APIs & Services → OAuth consent screen**:
   - Choose **External** (works for personal accounts)
   - Fill in App name, support email, and developer contact
   - On the **Scopes** step you can skip adding scopes manually — `auth_setup.py` requests them at runtime
   - On the **Test users** step, add your own Google account email
   - Leave the app in **Testing** mode (no need to publish)

   > **Note:** Apps in Testing mode issue refresh tokens that expire after 7 days of inactivity. If you get auth errors after a week of not using the server, re-run `python auth_setup.py`.

5. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Give it any name
   - Click **Download JSON**
6. Save the downloaded file as `client_secrets.json` in the project root (next to `auth_setup.py`)

### 2. Install

```bash
pip install -e .
```

Or with dev dependencies (for running tests):

```bash
pip install -e ".[dev]"
```

### 3. Authenticate

```bash
python auth_setup.py
```

Opens a browser for Google OAuth2 consent. Grant access to all requested scopes. On completion, saves the refresh token to Windows Credential Locker. Only needs to be run once (or again after a 7-day Testing-mode expiry).

You can also use the installed script alias:

```bash
g-api-mcp-auth
```

### 4. Register with Claude Code

Add the server to your Claude Code MCP config. The recommended approach is user-level registration so it's available in all projects.

**User-level (`~/.claude/mcp.json`):**

```json
{
  "mcpServers": {
    "g-api-mcp": {
      "command": "python",
      "args": ["-m", "g_api_mcp.server"],
      "cwd": "/path/to/g-api-mcp",
      "env": {
        "PYTHONPATH": "/path/to/g-api-mcp/src"
      }
    }
  }
}
```

Replace `/path/to/g-api-mcp` with the absolute path to this directory.

**Project-level (`.mcp.json` in the project root):**

Same format, but only active when that project is open in Claude Code.

After adding the config, restart Claude Code or run `/mcp` to reconnect.

### 5. Verify

In Claude Code, ask: *"List my Gmail labels"* — if the server is connected you'll see your labels returned.

## Security notes

- `client_secrets.json` is gitignored — never commit it
- The refresh token is stored in Windows Credential Locker (DPAPI-encrypted), not as a plaintext file
- `client_id` and `client_secret` are **not** stored in the credential store — they are read from `client_secrets.json` on every token refresh, so rotating the client secret in Cloud Console takes effect without re-authenticating
- All API calls are made locally — no data passes through any third-party proxy

## OAuth2 scopes requested

| Scope | Purpose |
|---|---|
| `gmail.modify` | Read inbox, search, apply/create/delete labels and filters |
| `gmail.send` | Send messages and manage drafts |
| `gmail.settings.basic` | Create and delete inbox filters |
| `calendar` | Read and write calendar events |
| `tasks` | Read and write tasks |
| `contacts` | Read and write Google Contacts (People API) |
