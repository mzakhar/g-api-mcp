---
topic: Google Tasks to Obsidian Vault Sync
phase: 3
depends_on: [phase-1, phase-2]
date: 2026-04-05
---

# SPEC Phase 3: Vault Write + Post-Sync Cleanup

## Objective

Implement the vault write layer (dedup upsert by `<!-- gtask:{id} -->`, section insertion),
the two-layer post-sync cleanup (notes marker + PATCH status=completed), and Windows Task
Scheduler registration for always-on background sync.

## Inputs required

- Phase 1: `sync.py` with `run_sync()`, `fetch_changed_tasks()`, state/config I/O
- Phase 2: `_to_vault_line()`, `_route_task()` implemented and tested
- `SyncConfig` with `vault_path`, `daily_notes_path`, `daily_note_section`
- **Auth scope check (required before Phase 3):** The existing stored credentials may only
  have `https://www.googleapis.com/auth/tasks.readonly`. PATCH and GET calls in cleanup
  require `https://www.googleapis.com/auth/tasks` (read-write). Run `python auth_setup.py`
  and confirm the `tasks` scope is in the OAuth consent. If not, re-authorize with the
  write scope added — this is a one-time step and invalidates the existing token.

## Deliverables

- `write_vault_task(line: str, target_file: Path, section: str)` — upsert by task ID
- `mark_synced_in_notes(task_id: str, tasklist_id: str, vault_path: str)` — notes PATCH
- `complete_task_in_google(task_id: str, tasklist_id: str)` — status=completed PATCH
- `run_sync()` wired end-to-end: fetch → transform → write → mark_notes → complete
- Windows Task Scheduler registration script `scripts/register-task-scheduler.ps1`
- Integration test `tests/test_sync_integration.py` (Layer 3+4 tests from Phase 4 spec)

## Boundaries

Always:
- Write vault files only after successful transform (never write a partial line)
- PATCH notes marker before PATCH status=completed (two-step, ordered)
- On any vault write failure: do NOT call mark_synced_in_notes or complete_task_in_google
- Atomic vault writes: write to `.tmp` file, rename to final path

Ask first:
- Any modification to existing vault notes beyond task line upsert
- Enabling `tasks` write scope if not already present in stored credentials

Never:
- Delete lines from vault notes (use `[-]` state instead)
- Call `tasks.delete` — only `tasks.patch` for cleanup
- Overwrite the entire target file — only append new sections or update specific lines

## Implementation steps

### 1. `write_vault_task(line: str, target_file: Path, section: str) -> bool`

Deduplication logic:
1. Extract task ID from line: `re.search(r'<!-- gtask:(\S+?) -->', line)`
2. If target_file exists: scan all lines for `<!-- gtask:{id} -->`.
   - If found: replace that line in-place (and any immediately-following sub-bullets up to
     the next line starting with `- ` or `  - ` at parent indent level)
   - If not found: proceed to insert
3. If inserting: find `section` header in file. Append task line after the header (before
   the next `##` header or EOF). If section header not found: append section + line at EOF.
4. If target_file does not exist: create it with minimal content (date heading + section),
   then insert.
5. Write atomically: `file.with_suffix('.tmp')`, then `Path.replace()`.
6. Return True on success, False on any IO error (caller logs and skips cleanup).

### 2. `mark_synced_in_notes(task_id: str, tasklist_id: str, vault_path: str)`

After confirmed vault write, append a sync marker to the task's notes field:
```
[synced-to-vault: 2026-04-05T10:30:00Z → {vault_path}]
```

```python
def mark_synced_in_notes(task_id: str, tasklist_id: str,
                          vault_path: str, timestamp: str):
    from googleapiclient.discovery import build
    from g_api_mcp.auth import cred_manager
    creds = cred_manager.get_credentials()
    service = build("tasks", "v1", credentials=creds)

    # Read current notes to avoid overwriting
    task = service.tasks().get(tasklist=tasklist_id, task=task_id).execute()
    current_notes = task.get("notes", "")
    marker = f"[synced-to-vault: {timestamp} → {vault_path}]"

    # Idempotency: skip if marker already present (same vault_path)
    if vault_path in current_notes:
        return

    new_notes = (current_notes + "\n" + marker).strip()
    service.tasks().patch(
        tasklist=tasklist_id,
        task=task_id,
        body={"notes": new_notes}
    ).execute()
```

### 3. `complete_task_in_google(task_id: str, tasklist_id: str)`

```python
def complete_task_in_google(task_id: str, tasklist_id: str):
    from googleapiclient.discovery import build
    from g_api_mcp.auth import cred_manager
    creds = cred_manager.get_credentials()
    service = build("tasks", "v1", credentials=creds)
    service.tasks().patch(
        tasklist=tasklist_id,
        task=task_id,
        body={"status": "completed"}
    ).execute()
    # Idempotent: re-patching a completed task is a no-op per API docs
```

**Recurring task guard**: Before calling `complete_task_in_google`, check if the task
re-appeared after a previous completion (same title in the list with a new ID). If detected,
use notes-only cleanup and log a warning. This is the practical signal for recurring tasks
since the v1 API has no recurrence field.

### 4. `run_sync()` — full orchestration

```
for each configured tasklist:
  tasks = fetch_changed_tasks(tasklist_id, state.last_sync[tasklist_id])
  for each task:
    if task is in state.processed_tasks with same updated timestamp:
      skip (already processed)
    line = _to_vault_line(task)
    target_file, section = _route_task(task, tasklist_config, config, today)
    success = write_vault_task(line, target_file, section)
    if success:
      mark_synced_in_notes(task.id, tasklist_id, str(target_file))
      complete_task_in_google(task.id, tasklist_id)
      state.processed_tasks[task.id] = {updated: task.updated, vault_path: str(target_file)}
    else:
      log error, continue to next task
  state.last_sync[tasklist_id] = now()
save_state(state)
```

### 5. Windows Task Scheduler registration

Create `scripts/register-task-scheduler.ps1`:

```powershell
# Register g-api-mcp-sync as a Windows Task Scheduler job
# Run once as the current user (no admin required for user-level tasks)
# Usage: .\register-task-scheduler.ps1

$TaskName = "g-api-mcp-sync"
$PythonExe = (Get-Command g-api-mcp-sync -ErrorAction Stop).Source

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe

$Trigger = New-ScheduledTaskTrigger `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -Once `
    -At (Get-Date)

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Limited `
    -Force

Write-Host "Registered '$TaskName' — runs every 1 minute as current user."
Write-Host "View logs: Get-ScheduledTaskInfo -TaskName '$TaskName'"
```

Note: Windows Task Scheduler minimum interval is 1 minute, which equals the recommended
60-second poll interval from the research findings.

## Validation criteria

- [ ] `write_vault_task` with a new task ID creates the task line in the target section
- [ ] `write_vault_task` called twice with same task ID updates the line, no duplicate
- [ ] `write_vault_task` with a non-existent daily note file creates the file
- [ ] Vault write failure (read-only file) returns False and does NOT call cleanup
- [ ] `mark_synced_in_notes` appends marker to task notes without overwriting existing notes
- [ ] `mark_synced_in_notes` called twice for same vault_path is idempotent (no double marker)
- [ ] `complete_task_in_google` on an already-completed task does not raise an exception
- [ ] Full `run_sync()` end-to-end: 3-task fixture → vault file contains 3 lines, all 3 tasks patched in Google
- [ ] `run_sync()` called twice (idempotency): vault file still has 3 lines (no duplicates), API patch called only once per task
- [ ] Task Scheduler script runs without error on developer machine
- [ ] Task Scheduler job visible in Task Scheduler UI after registration

## Code patterns / examples

```python
# Atomic vault write helper
import os
from pathlib import Path

def _atomic_write(path: Path, content: str):
    """Write content to path atomically via temp file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)  # atomic on same filesystem

def write_vault_task(line: str, target_file: Path, section: str) -> bool:
    import re
    try:
        # Extract task ID for dedup
        m = re.search(r'<!-- gtask:(\S+?) -->', line)
        if not m:
            return False
        task_id = m.group(1)

        if target_file.exists():
            text = target_file.read_text(encoding="utf-8")
        else:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            date_str = target_file.stem  # "2026-04-05"
            text = f"# {date_str}\n\n{section}\n\n"

        # Check for existing entry
        existing_pattern = re.compile(
            rf'^(  )*- \[[ x/>\-]\].*<!-- gtask:{re.escape(task_id)} -->',
            re.MULTILINE
        )
        if existing_pattern.search(text):
            new_text = existing_pattern.sub(line, text)
        else:
            # Find section and append
            sec_pattern = re.compile(rf'^{re.escape(section)}', re.MULTILINE)
            m2 = sec_pattern.search(text)
            if m2:
                # Insert after section header line
                insert_pos = text.index('\n', m2.start()) + 1
                new_text = text[:insert_pos] + line + '\n' + text[insert_pos:]
            else:
                new_text = text.rstrip('\n') + f'\n\n{section}\n\n{line}\n'

        _atomic_write(target_file, new_text)
        return True
    except OSError:
        return False
```
