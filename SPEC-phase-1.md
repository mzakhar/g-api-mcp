---
topic: Google Tasks to Obsidian Vault Sync
phase: 1
depends_on: []
date: 2026-04-05
---

# SPEC Phase 1: Polling Infrastructure

## Objective

Stand up the core sync loop: poll Google Tasks with incremental change detection, persist
state between runs, and expose the runner as both a CLI entry point and an MCP tool stub.

## Inputs required

- Existing `src/g_api_mcp/` package (auth.py, tasks.py, server.py)
- `pyproject.toml` (to add console script entry point)

## Deliverables

- `src/g_api_mcp/sync.py` — core sync module (polling loop, state I/O, config I/O)
- `%APPDATA%/g-api-mcp/sync-config.json` — user configuration (vault path, tasklist IDs, project map)
- `%APPDATA%/g-api-mcp/sync-state.json` — runtime state (last sync timestamps per tasklist)
- `pyproject.toml` entry point: `g-api-mcp-sync = "g_api_mcp.sync:main"`
- Stub `@mcp.tool()` in `server.py` that calls `sync.run_sync()` (body: pass for now)

## Boundaries

Always:
- Read/write only to `%APPDATA%/g-api-mcp/` for state and config
- Use existing `cred_manager` from `auth.py` — do not create a new auth flow
- Use `asyncio.to_thread()` for Google API calls (existing pattern)

Ask first:
- Any changes to `server.py` that affect existing MCP tools
- Adding new pip dependencies beyond what's in pyproject.toml

Never:
- Hardcode vault path or tasklist IDs — these must come from sync-config.json
- Write to the vault in Phase 1 (vault write is Phase 3)

## Implementation steps

1. Create `src/g_api_mcp/sync.py` with:
   - `DEFAULT_CONFIG_DIR = Path(os.environ["APPDATA"]) / "g-api-mcp"`
   - `CONFIG_PATH = DEFAULT_CONFIG_DIR / "sync-config.json"`
   - `STATE_PATH = DEFAULT_CONFIG_DIR / "sync-state.json"`
   - `load_config() -> SyncConfig` — reads CONFIG_PATH, raises with helpful message if missing
   - `load_state() -> SyncState` — reads STATE_PATH, returns empty state if not found
   - `save_state(state: SyncState)` — atomic write (write to .tmp, rename)
   - `fetch_changed_tasks(tasklist_id: str, updated_min: str | None) -> list[dict]` — calls
     `tasks.list` with `updatedMin`, `showDeleted=True`, `showHidden=True`,
     `showCompleted=True`, paginates via `nextPageToken`
   - `run_sync(config: SyncConfig, state: SyncState) -> SyncResult` — main loop, calls
     `fetch_changed_tasks` per configured tasklist, returns counts (new/updated/deleted/errors)
   - `main()` — entry point: load config, load state, call run_sync, save state, print summary

2. Add `tasks_sync_to_vault` stub to `server.py`:
   ```python
   @mcp.tool()
   async def tasks_sync_to_vault() -> str:
       """Sync Google Tasks to Obsidian vault. Returns summary of changes."""
       from g_api_mcp.sync import load_config, load_state, run_sync, save_state
       config = load_config()
       state = load_state()
       result = await asyncio.to_thread(run_sync, config, state)
       save_state(result.new_state)
       return json.dumps(build_envelope(result.summary))
   ```

3. Add to `pyproject.toml` under `[project.scripts]`:
   ```
   g-api-mcp-sync = "g_api_mcp.sync:main"
   ```

4. Create `sync-config.json` schema (write a `sync-config.example.json` to the repo):
   ```json
   {
     "vault_path": "C:\\Obsidian\\Hivemind",
     "task_lists": [
       {
         "id": "MDIwMTY4NjM4NTYyNzc4MzI2NzI6MDow",
         "name": "My Tasks",
         "project_note": null
       },
       {
         "id": "<list-id>",
         "name": "Work",
         "project_note": "20 Projects/Work/Work.md"
       }
     ],
     "poll_interval_seconds": 60,
     "daily_notes_path": "00 Daily Plan",
     "daily_note_section": "## Top Priorities"
   }
   ```

5. Create `sync-state.json` schema:
   ```json
   {
     "last_sync": {
       "<tasklist_id>": "2026-04-05T10:00:00Z"
     },
     "processed_tasks": {
       "<task_id>": {
         "updated": "2026-04-05T09:00:00Z",
         "vault_path": "00 Daily Plan/2026-04-05.md"
       }
     }
   }
   ```

## Validation criteria

- [ ] `pip install -e .` succeeds after pyproject.toml edit
- [ ] `g-api-mcp-sync --help` runs without error
- [ ] `g-api-mcp-sync` with missing config prints a helpful error message (not a stack trace)
- [ ] `fetch_changed_tasks` with a future `updatedMin` returns an empty list
- [ ] `fetch_changed_tasks` with `updatedMin=None` returns all tasks in the list
- [ ] State file is written atomically (no partial write on KeyboardInterrupt)
- [ ] MCP server still starts and all existing tools respond after server.py stub is added

## Code patterns / examples

```python
# src/g_api_mcp/sync.py — fetch_changed_tasks
import os
from pathlib import Path
from datetime import datetime, timezone
from googleapiclient.discovery import build
from g_api_mcp.auth import cred_manager

def fetch_changed_tasks(tasklist_id: str, updated_min: str | None) -> list[dict]:
    """Fetch all tasks modified after updated_min (RFC 3339). None = fetch all."""
    creds = cred_manager.get_credentials()
    service = build("tasks", "v1", credentials=creds)

    params = {
        "tasklist": tasklist_id,
        "showDeleted": True,
        "showHidden": True,
        "showCompleted": True,
        "maxResults": 100,
    }
    if updated_min:
        params["updatedMin"] = updated_min

    tasks = []
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        response = service.tasks().list(**params).execute()
        tasks.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return tasks
```
