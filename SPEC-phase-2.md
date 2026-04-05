---
topic: Google Tasks to Obsidian Vault Sync
phase: 2
depends_on: [phase-1]
date: 2026-04-05
---

# SPEC Phase 2: Transformation + Routing

## Objective

Implement `_to_vault_line()` (Google Task dict → vault task markdown string) and the routing
algorithm (which note file + which section the task line belongs in). Phase 1's `run_sync()`
calls these but receives stub results until this phase is complete.

## Inputs required

- Phase 1 complete: `sync.py` with `run_sync()` calling transform/route stubs
- `SyncConfig` with `vault_path`, `task_lists[].project_note`, `daily_notes_path`
- Track 2 findings: vault task syntax reference (`30 Library/Task Management System.md`)
- Track 3 findings: complete field mapping table and routing algorithm

## Deliverables

- `_to_vault_line(task: dict) -> str` in `sync.py`
- `_route_task(task: dict, tasklist: TaskListConfig, config: SyncConfig, state_date: date) -> tuple[Path, str]`
  returns `(target_file_path, section_header)`
- Keyword lists for priority inference and due-date semantic classification
- Unit test file `tests/test_sync_transform.py` with the Layer 1 corpus (tests 1–7 from Phase 4 spec)

## Boundaries

Always:
- `⏳` for all date markers from Google Tasks — never `📅` (would corrupt Overdue dashboard)
- Default priority `🔼` for all incoming tasks
- Embed `<!-- gtask:{id} -->` at end of every task line (including subtasks)
- Use only existing project notes — never create new project note files

Ask first:
- Any changes to the routing algorithm that would move tasks to weekly notes (not in current spec)

Never:
- Use `📅` for Google Task due dates
- Infer `📅` from keywords (reserved for future version after user validation)
- Create vault note files that don't already exist (except daily notes — see step 4)

## Implementation steps

### 1. Field mapping — `_to_vault_line(task: dict) -> str`

Complete mapping table:

| Google Task field | Vault output | Rule |
|---|---|---|
| `id` | `<!-- gtask:{id} -->` at end of line | Deterministic |
| `title` | Description text | Deterministic (text); heuristic (priority) |
| `status=needsAction` | `- [ ]` | Deterministic |
| `status=completed` | `- [x]` | Deterministic |
| `deleted=true` | `- [-]` | Deterministic |
| `due` (RFC3339 date) | `⏳ YYYY-MM-DD` | Deterministic (always ⏳) |
| `completed` timestamp | `✅ YYYY-MM-DD` appended | Deterministic |
| `notes` ≤80 chars, no newline | ` — {notes}` inline after title | Heuristic |
| `notes` >80 chars or has newline | Sub-bullets on next lines (2-space indent) | Heuristic |
| `parent` non-null | 2-space indent prefix | Heuristic (parent lookup) |
| `links[]` non-empty | Sub-bullets: `  - [{description}]({url})` | Deterministic |
| `updated`, `etag`, `position` | Ignored | — |

Priority assignment (heuristic — no priority field in Google Tasks API):
- Default: `🔼`
- Downgrade to `🔽` if title contains: "maybe", "someday", "idea", "consider", "explore"
- Keep `🔼` for everything else
- Never auto-assign `⏫` (hard commit) — user must promote manually in vault

System tag assignment:
- Append `#someday` if: no due date AND (title contains someday/idea keywords OR `hidden=true`)
- Append `#waiting` if: title contains "waiting for", "blocked by", "pending" AND has a due date
- Never auto-assign `#theirs` or `#agenda` (require human judgment)

### 2. Routing algorithm — `_route_task(...) -> tuple[Path, str]`

```
1. Tasklist has explicit project_note configured?
   → route to that file, section "## Work Plan"
   
2. No explicit project_note:
   a. Task has a due date?
      → route to {vault_path}/{daily_notes_path}/{due_date_YYYY-MM-DD}.md
      → section = config.daily_note_section  (default: "## Top Priorities")
   b. Task has no due date and has #someday:
      → route to today's daily note
      → section = "## Someday / Maybe"
   c. Task has no due date, no #someday:
      → route to today's daily note
      → section = config.daily_note_section
```

Daily note creation: if the target daily note file does not exist, create it with minimal
content (date heading only — do not use a template, as the template is for `/plan-today`):
```markdown
# {YYYY-MM-DD}

## Top Priorities

## Someday / Maybe
```

### 3. Subtask handling

Subtasks have a non-null `parent` field. Subtask routing:
1. Look up parent task ID in `state.processed_tasks` to find its vault_path.
2. If found: same file as parent, indented 2 spaces, inserted immediately after parent line.
3. If not found (parent not yet synced): route to same target as a standalone task, prefix
   title with `[Subtask] `, add `<!-- gtask-parent:{parent_id} -->` comment. A subsequent
   sync pass that processes the parent will re-parent it.

### 4. Normalized list-name tag

When a tasklist has no configured `project_note`, append a normalized tag derived from the
list name: lowercase, spaces→hyphens, strip non-alphanumeric. Examples:
- "My Tasks" → (no tag, default list)
- "Work Projects" → `#work-projects`
- "Personal" → `#personal`

Skip the tag for the default "My Tasks" list to avoid tag noise.

## Validation criteria

- [ ] `_to_vault_line({"id": "abc", "title": "Review PR", "status": "needsAction", "due": "2026-04-10T00:00:00.000Z"})` returns `- [ ] 🔼 Review PR ⏳ 2026-04-10 <!-- gtask:abc -->`
- [ ] Completed task output contains `[x]` and `✅ YYYY-MM-DD`
- [ ] Task with no due date and "someday" in title gets `#someday`, no date marker
- [ ] Task with `notes` >80 chars renders notes as sub-bullet, not inline
- [ ] Task with `deleted=True` renders as `[-]`
- [ ] `_route_task` for a task with `due=2026-04-10` and no project_note returns path `00 Daily Plan/2026-04-10.md`
- [ ] `_route_task` for a tasklist with explicit `project_note` returns that path regardless of due date
- [ ] Subtask with unknown parent is prefixed with `[Subtask]` and has `<!-- gtask-parent:{id} -->`
- [ ] All 7 Layer 1 unit tests in `tests/test_sync_transform.py` pass

## Code patterns / examples

```python
# src/g_api_mcp/sync.py — _to_vault_line

import re
from datetime import date as Date

_SOMEDAY_KEYWORDS = {"maybe", "someday", "idea", "consider", "explore"}
_WAITING_KEYWORDS = {"waiting for", "blocked by", "pending"}

def _to_vault_line(task: dict, indent: int = 0) -> str:
    """Convert a Google Task dict to an Obsidian Tasks-compatible markdown line."""
    task_id = task["id"]
    title = task.get("title", "(untitled)").strip() or "(untitled)"
    status = task.get("status", "needsAction")
    deleted = task.get("deleted", False)
    due_raw = task.get("due")           # "2026-04-10T00:00:00.000Z" or None
    completed_raw = task.get("completed")
    notes = task.get("notes", "")

    # Checkbox state
    if deleted:
        checkbox = "- [-]"
    elif status == "completed":
        checkbox = "- [x]"
    else:
        checkbox = "- [ ]"

    # Priority
    title_lower = title.lower()
    if any(kw in title_lower for kw in _SOMEDAY_KEYWORDS):
        priority = "🔽"
    else:
        priority = "🔼"

    # Date
    date_part = ""
    if due_raw and not deleted:
        due_date = due_raw[:10]  # "2026-04-10"
        date_part = f" ⏳ {due_date}"

    # Completion stamp
    completion_part = ""
    if completed_raw and status == "completed":
        completion_date = completed_raw[:10]
        completion_part = f" ✅ {completion_date}"

    # System tags
    tags = []
    if not due_raw and any(kw in title_lower for kw in _SOMEDAY_KEYWORDS):
        tags.append("#someday")
    if any(kw in title_lower for kw in _WAITING_KEYWORDS) and due_raw:
        tags.append("#waiting")
    tag_part = (" " + " ".join(tags)) if tags else ""

    # Task ID comment (deduplication anchor)
    id_comment = f" <!-- gtask:{task_id} -->"

    # Inline notes or sub-bullets handled by caller
    body = f"{checkbox} {priority} {title}{date_part}{completion_part}{tag_part}{id_comment}"

    prefix = "  " * indent
    lines = [prefix + body]

    # Notes
    if notes:
        if len(notes) <= 80 and "\n" not in notes:
            # Inline: insert notes before the trailing ID comment
            lines[0] = lines[0].removesuffix(id_comment) + f" — {notes}{id_comment}"
        else:
            for note_line in notes.splitlines():
                if note_line.strip():
                    lines.append(f"{prefix}  - {note_line.strip()}")

    return "\n".join(lines)
```
