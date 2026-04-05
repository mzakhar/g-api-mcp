# g-api-mcp

A local stdio MCP server wrapping Gmail, Google Calendar, and Google Tasks for use with Claude Code and Claude Desktop.

## Features

- **22 tools** across Gmail (7), Calendar (7), Tasks (7), and Sync (1)
- **Context-efficient responses** — every tool returns a JSON envelope with `pagination` and `context_hint.estimated_tokens` so the LLM can plan before fetching more data
- **List-then-fetch pattern** — list tools return thin summaries (IDs + metadata, no body); get tools fetch full content for specific IDs on demand
- **Cursor-based pagination** — all list tools accept `page_cursor` and return `pagination.next_cursor`
- **Secure credential storage** — refresh token stored in Windows Credential Locker via `keyring`; client identity read fresh from `client_secrets.json` on every refresh (supports client secret rotation without re-auth)

## Tools

### Gmail
| Tool | Description |
|---|---|
| `gmail_list_messages` | Search/list messages — returns thin summaries only |
| `gmail_get_message` | Full content (body, headers, attachment metadata) for one message |
| `gmail_send_message` | Compose and send; supports threaded replies |
| `gmail_create_draft` | Save to Drafts without sending |
| `gmail_list_labels` | All labels (system + user-created) |
| `gmail_modify_message` | Add/remove labels (mark read, star, archive, etc.) |
| `gmail_get_attachment` | Download an attachment to a local path |

### Calendar
| Tool | Description |
|---|---|
| `calendar_list_calendars` | All calendars with IDs and access roles |
| `calendar_list_events` | Events in a time range — thin summaries |
| `calendar_get_event` | Full event (attendees, conference link, recurrence) |
| `calendar_create_event` | Create an event with optional attendees and Meet link |
| `calendar_update_event` | Partial update (PATCH) — only supplied fields change |
| `calendar_delete_event` | Delete with optional cancellation notices |
| `calendar_quick_add` | Natural-language event creation |

### Tasks
| Tool | Description |
|---|---|
| `tasks_list_tasklists` | All task lists with IDs |
| `tasks_list_tasks` | Tasks in a list — thin summaries, filterable by due date |
| `tasks_get_task` | Full task (notes, links, parent/subtask info) |
| `tasks_create_task` | Create a task, optionally as a subtask |
| `tasks_update_task` | Partial update — title, notes, due date, status |
| `tasks_complete_task` | Mark done and stamp completion time |
| `tasks_delete_task` | Permanently delete a task |

### Sync

| Tool | Description |
|---|---|
| `tasks_sync_to_vault` | Fetch tasks changed since last sync, write them to the Obsidian vault as formatted task lines, and mark them complete in Google Tasks |

The sync tool is driven by a config file at `%APPDATA%\g-api-mcp\sync-config.json`. Copy `sync-config.example.json` from the project root and edit it:

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

The sync can also be run as a standalone CLI:

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

### 1. Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create or select a project
3. **APIs & Services → Library** — enable:
   - Gmail API
   - Google Calendar API
   - Tasks API
4. **APIs & Services → OAuth consent screen** — configure (External or Internal), add yourself as a test user
5. **APIs & Services → Credentials → Create → OAuth client ID → Desktop app** → download JSON
6. Save the downloaded file as `client_secrets.json` in this directory

### 2. Install

```bash
pip install -e .
```

### 3. Authenticate

```bash
python auth_setup.py
```

Opens a browser for Google OAuth2 consent. On completion, saves the refresh token to Windows Credential Locker. Only needs to be run once (or again if the refresh token expires).

### 4. Register with Claude Code

The `.mcp.json` in this directory registers the server automatically when you open this project in Claude Code. No further configuration needed.

To register globally (available in all projects), add the `mcpServers` block from `.mcp.json` to `~/.claude/settings.json`.

## Security notes

- `client_secrets.json` is gitignored — never commit it
- The refresh token is stored in Windows Credential Locker (DPAPI-encrypted), not as a plaintext file
- `client_id` and `client_secret` are **not** stored in the credential store — they are read from `client_secrets.json` on every token refresh, so rotating the client secret in Cloud Console takes effect without re-authenticating

## OAuth2 scopes requested

| Scope | Purpose |
|---|---|
| `gmail.modify` | Read inbox, search, apply labels |
| `gmail.send` | Send messages and manage drafts |
| `calendar` | Read and write calendar events |
| `tasks` | Read and write tasks |
