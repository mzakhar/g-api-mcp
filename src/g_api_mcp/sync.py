"""
Google Tasks → Obsidian vault sync module.

Fetches tasks modified since the last sync, renders them as Obsidian-formatted
task lines, upserts them into the correct vault file/section, and marks them
as completed in Google Tasks.

CLI entry point: g-api-mcp-sync
MCP tool: tasks_sync_to_vault
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

from g_api_mcp.auth import cred_manager

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config / State paths
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "g-api-mcp"
CONFIG_PATH = CONFIG_DIR / "sync-config.json"
STATE_PATH = CONFIG_DIR / "sync-state.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TaskListConfig:
    id: str
    name: str
    project_note: str | None = None  # relative to vault_path, e.g. "20 Projects/Work/Work.md"


@dataclass
class SyncConfig:
    vault_path: Path
    task_lists: list[TaskListConfig]
    poll_interval_seconds: int = 60
    daily_notes_path: str = "00 Daily Plan"
    daily_note_section: str = "## Top Priorities"


@dataclass
class ProcessedTask:
    updated: str  # RFC 3339
    vault_path: str  # relative to vault_path


@dataclass
class SyncState:
    last_sync: dict[str, str] = field(default_factory=dict)  # tasklist_id -> RFC 3339
    processed_tasks: dict[str, ProcessedTask] = field(default_factory=dict)  # task_id -> ProcessedTask


# ---------------------------------------------------------------------------
# Config / State I/O
# ---------------------------------------------------------------------------


def load_config() -> SyncConfig:
    """Load sync configuration from CONFIG_PATH.

    Raises FileNotFoundError with a helpful message if the file doesn't exist.
    """
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Sync config not found at {CONFIG_PATH}.\n"
            "Create it by copying sync-config.example.json from the project root:\n"
            f"  copy sync-config.example.json \"{CONFIG_PATH}\"\n"
            "Then edit it with your vault path and task list IDs."
        )

    with CONFIG_PATH.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    task_lists = [
        TaskListConfig(
            id=tl["id"],
            name=tl["name"],
            project_note=tl.get("project_note"),
        )
        for tl in raw.get("task_lists", [])
    ]

    return SyncConfig(
        vault_path=Path(raw["vault_path"]),
        task_lists=task_lists,
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 60)),
        daily_notes_path=raw.get("daily_notes_path", "00 Daily Plan"),
        daily_note_section=raw.get("daily_note_section", "## Top Priorities"),
    )


def load_state() -> SyncState:
    """Load sync state from STATE_PATH. Returns empty SyncState if file doesn't exist."""
    if not STATE_PATH.exists():
        return SyncState()

    with STATE_PATH.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    processed: dict[str, ProcessedTask] = {}
    for task_id, pt in raw.get("processed_tasks", {}).items():
        processed[task_id] = ProcessedTask(
            updated=pt["updated"],
            vault_path=pt["vault_path"],
        )

    return SyncState(
        last_sync=raw.get("last_sync", {}),
        processed_tasks=processed,
    )


def save_state(state: SyncState) -> None:
    """Atomically write state to STATE_PATH."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Serialize ProcessedTask objects to dicts
    raw_processed = {
        task_id: asdict(pt)
        for task_id, pt in state.processed_tasks.items()
    }
    raw = {
        "last_sync": state.last_sync,
        "processed_tasks": raw_processed,
    }

    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ---------------------------------------------------------------------------
# Google Tasks fetching
# ---------------------------------------------------------------------------


def fetch_changed_tasks(service: Any, tasklist_id: str, updated_min: str | None) -> list[dict]:
    """Fetch all tasks changed since updated_min (paginated).

    Synchronous — intended to be called via asyncio.to_thread.
    """
    all_tasks: list[dict] = []
    page_token: str | None = None

    while True:
        kwargs: dict[str, Any] = {
            "tasklist": tasklist_id,
            "showDeleted": True,
            "showHidden": True,
            "showCompleted": True,
            "maxResults": 100,
        }
        if updated_min:
            kwargs["updatedMin"] = updated_min
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.tasks().list(**kwargs).execute()
        all_tasks.extend(result.get("items", []))

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return all_tasks


# ---------------------------------------------------------------------------
# Vault line rendering
# ---------------------------------------------------------------------------

_SOMEDAY_KEYWORDS = frozenset({"maybe", "someday", "idea", "consider", "explore"})
_WAITING_KEYWORDS = frozenset({"waiting for", "blocked by", "pending"})


def _to_vault_line(task: dict, indent: int = 0) -> str:
    task_id = task["id"]
    title = (task.get("title") or "").strip() or "(untitled)"
    status = task.get("status", "needsAction")
    deleted = task.get("deleted", False)
    due_raw = task.get("due")
    completed_raw = task.get("completed")
    notes = task.get("notes") or ""

    # Checkbox
    if deleted:
        checkbox = "[-]"
    elif status == "completed":
        checkbox = "[x]"
    else:
        checkbox = "[ ]"

    # Priority (heuristic — no priority field in Google Tasks)
    title_lower = title.lower()
    priority = "🔽" if any(kw in title_lower for kw in _SOMEDAY_KEYWORDS) else "🔼"

    # Date — ALWAYS ⏳, never 📅 (vault Overdue dashboard uses 📅 for hard deadlines only)
    date_part = f" ⏳ {due_raw[:10]}" if (due_raw and not deleted) else ""

    # Completion stamp
    completion_part = f" ✅ {completed_raw[:10]}" if (completed_raw and status == "completed") else ""

    # System tags
    tags = []
    if not due_raw and any(kw in title_lower for kw in _SOMEDAY_KEYWORDS):
        tags.append("#someday")
    if due_raw and any(kw in title_lower for kw in _WAITING_KEYWORDS):
        tags.append("#waiting")
    tag_part = (" " + " ".join(tags)) if tags else ""

    # Deduplication anchor — invisible to Obsidian Tasks plugin queries
    id_comment = f" <!-- gtask:{task_id} -->"

    prefix = "  " * indent
    body = f"{prefix}- {checkbox} {priority} {title}{date_part}{completion_part}{tag_part}{id_comment}"

    lines = [body]

    # Notes rendering
    if notes:
        if len(notes) <= 80 and "\n" not in notes:
            # Inline: insert before the ID comment
            lines[0] = lines[0].removesuffix(id_comment) + f" — {notes}{id_comment}"
        else:
            for note_line in notes.splitlines():
                if note_line.strip():
                    lines.append(f"{prefix}  - {note_line.strip()}")

    # Links
    for link in task.get("links", []):
        desc = link.get("description", "link")
        url = link.get("link", "")
        lines.append(f"{prefix}  - [{desc}]({url})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _route_task(
    task: dict,
    tl_config: TaskListConfig,
    config: SyncConfig,
    today: date,
) -> tuple[Path, str]:
    """Determine target vault file and section for a task.

    Returns (absolute_target_file_path, section_header_string).
    """
    if tl_config.project_note is not None:
        return (config.vault_path / tl_config.project_note, "## Work Plan")

    due_raw = task.get("due")
    if due_raw:
        due_date = due_raw[:10]
        target = config.vault_path / config.daily_notes_path / f"{due_date}.md"
        return (target, config.daily_note_section)

    # No due date — goes to today's note under Someday / Maybe
    target = config.vault_path / config.daily_notes_path / f"{today.isoformat()}.md"
    return (target, "## Someday / Maybe")


# ---------------------------------------------------------------------------
# Vault upsert
# ---------------------------------------------------------------------------

_GTASK_ID_RE = re.compile(r"<!-- gtask:(\S+?) -->")


def write_vault_task(line: str, target_file: Path, section: str) -> bool:
    """Upsert a task line into the vault file under the given section.

    Returns True on success, False on OSError.
    """
    # Extract task ID from line
    m = _GTASK_ID_RE.search(line)
    if not m:
        log.error("No gtask ID found in line: %r", line)
        return False
    task_id = m.group(1)

    try:
        if target_file.exists():
            text = target_file.read_text(encoding="utf-8")
        else:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            text = f"# {target_file.stem}\n\n{section}\n\n"

        # Look for existing task line
        existing_pattern = re.compile(
            rf"^(  )*- \[[ x/>\-]\].*<!-- gtask:{re.escape(task_id)} -->",
            re.MULTILINE,
        )
        existing_match = existing_pattern.search(text)

        if existing_match:
            # Replace the matched line plus any indented sub-bullets that follow
            start = existing_match.start()
            end = existing_match.end()

            # Advance past the matched line end
            next_newline = text.find("\n", end)
            if next_newline == -1:
                # Match is at end of file
                block_end = len(text)
            else:
                block_end = next_newline + 1
                # Collect subsequent indented lines (sub-bullets)
                while block_end < len(text):
                    rest = text[block_end:]
                    # A sub-bullet starts with at least one space followed by "-"
                    sub_match = re.match(r"^  +- ", rest)
                    if sub_match:
                        nl = rest.find("\n")
                        if nl == -1:
                            block_end = len(text)
                            break
                        block_end += nl + 1
                    else:
                        break

            text = text[:start] + line + "\n" + text[block_end:]
        else:
            # Insert after section header
            section_idx = text.find(section)
            if section_idx == -1:
                # Section not present — append it
                if not text.endswith("\n"):
                    text += "\n"
                text += f"\n{section}\n\n{line}\n"
            else:
                # Find end of section header line
                header_end = text.find("\n", section_idx)
                if header_end == -1:
                    text += f"\n\n{line}\n"
                else:
                    insert_pos = header_end + 1
                    # Skip blank lines immediately after header
                    while insert_pos < len(text) and text[insert_pos] == "\n":
                        insert_pos += 1
                    # Find the next ## section or EOF to know the insertion boundary
                    next_section = re.search(r"^##", text[insert_pos:], re.MULTILINE)
                    if next_section:
                        # Insert at insert_pos (right after header + blanks, before next section)
                        text = text[:insert_pos] + line + "\n\n" + text[insert_pos:]
                    else:
                        # No next section — append at insert_pos
                        text = text[:insert_pos] + line + "\n" + text[insert_pos:]

        # Atomic write
        tmp = target_file.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target_file)
        return True

    except OSError as e:
        log.error("Vault write failed for %s: %s", target_file, e)
        return False


# ---------------------------------------------------------------------------
# Google Tasks cleanup
# ---------------------------------------------------------------------------


def _mark_synced_in_notes(
    service: Any,
    task_id: str,
    tasklist_id: str,
    vault_rel_path: str,
    timestamp: str,
) -> None:
    """Append a synced-to-vault marker to the task's notes field.

    Synchronous — intended to be called via asyncio.to_thread.
    Idempotent: no-op if the marker is already present.
    """
    task = service.tasks().get(tasklist=tasklist_id, task=task_id).execute()
    existing_notes = task.get("notes") or ""
    marker = f"[synced-to-vault: {timestamp} → {vault_rel_path}]"

    if vault_rel_path in existing_notes:
        return

    new_notes = f"{existing_notes}\n{marker}".lstrip("\n") if existing_notes else marker
    service.tasks().patch(
        tasklist=tasklist_id,
        task=task_id,
        body={"notes": new_notes},
    ).execute()


def _complete_task_in_google(service: Any, task_id: str, tasklist_id: str) -> None:
    """Mark a task as completed in Google Tasks.

    Synchronous — intended to be called via asyncio.to_thread.
    Idempotent (re-patching a completed task is a no-op per the API).
    """
    service.tasks().patch(
        tasklist=tasklist_id,
        task=task_id,
        body={"status": "completed"},
    ).execute()


# ---------------------------------------------------------------------------
# Core sync orchestration
# ---------------------------------------------------------------------------


def run_sync(config: SyncConfig, state: SyncState) -> tuple[SyncState, dict]:
    """Fetch changed tasks, write to vault, mark completed in Google Tasks.

    Returns (new_state, summary_dict).
    Synchronous — intended to be called via asyncio.to_thread from the MCP tool.
    """
    creds = cred_manager.get_valid_credentials()
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)

    today = date.today()
    now = datetime.now(timezone.utc).isoformat()
    summary: dict[str, Any] = {"processed": 0, "skipped": 0, "errors": 0, "by_list": {}}
    new_processed = dict(state.processed_tasks)
    new_last_sync = dict(state.last_sync)

    for tl in config.task_lists:
        updated_min = state.last_sync.get(tl.id)
        list_summary: dict[str, int] = {"processed": 0, "skipped": 0, "errors": 0}

        try:
            tasks = fetch_changed_tasks(service, tl.id, updated_min)
        except Exception as e:
            log.error("Failed to fetch tasks for list %r: %s", tl.name, e)
            summary["errors"] += 1
            continue

        for task in tasks:
            task_id = task["id"]
            task_updated = task.get("updated", "")

            # Skip if already processed at this version
            prev = new_processed.get(task_id)
            if prev and prev.updated == task_updated:
                list_summary["skipped"] += 1
                summary["skipped"] += 1
                continue

            # Skip tasks we marked completed ourselves
            if (
                task.get("status") == "completed"
                and "synced-to-vault" in task.get("notes", "")
            ):
                if task_id in new_processed:
                    list_summary["skipped"] += 1
                    summary["skipped"] += 1
                    continue

            try:
                indent = 1 if task.get("parent") else 0
                line = _to_vault_line(task, indent=indent)
                target_file, section = _route_task(task, tl, config, today)

                success = write_vault_task(line, target_file, section)
                if not success:
                    log.error("Vault write failed for task %r", task_id)
                    list_summary["errors"] += 1
                    summary["errors"] += 1
                    continue

                vault_rel = str(target_file.relative_to(config.vault_path))

                # Two-layer cleanup: notes marker first, then complete
                try:
                    _mark_synced_in_notes(service, task_id, tl.id, vault_rel, now)
                    _complete_task_in_google(service, task_id, tl.id)
                except Exception as e:
                    log.warning(
                        "Cleanup failed for task %r: %s — vault write succeeded",
                        task_id,
                        e,
                    )

                new_processed[task_id] = ProcessedTask(
                    updated=task_updated, vault_path=vault_rel
                )
                list_summary["processed"] += 1
                summary["processed"] += 1

            except Exception as e:
                log.error("Error processing task %r: %s", task_id, e)
                list_summary["errors"] += 1
                summary["errors"] += 1

        new_last_sync[tl.id] = now
        summary["by_list"][tl.name] = list_summary

    new_state = SyncState(last_sync=new_last_sync, processed_tasks=new_processed)
    return new_state, summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    new_state, summary = run_sync(config, state)
    save_state(new_state)

    print(
        f"Sync complete: {summary['processed']} processed, "
        f"{summary['skipped']} skipped, {summary['errors']} errors"
    )
    if summary["errors"]:
        sys.exit(1)


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

from g_api_mcp.server import mcp  # noqa: E402


@mcp.tool()
async def tasks_sync_to_vault() -> str:
    """Sync Google Tasks to Obsidian vault.
    Fetches tasks modified since the last sync, writes them to the vault as
    properly-formatted tasks, and marks them as completed in Google Tasks.
    Returns a summary of changes made.
    """
    import asyncio
    from g_api_mcp.envelope import build_envelope, error_envelope

    try:
        config = load_config()
    except FileNotFoundError as e:
        return error_envelope(str(e))

    state = load_state()
    try:
        new_state, summary = await asyncio.to_thread(run_sync, config, state)
    except Exception as e:
        return error_envelope(f"Sync failed: {e}")

    save_state(new_state)
    env = build_envelope(data=summary, is_list=False)
    return json.dumps(env)
