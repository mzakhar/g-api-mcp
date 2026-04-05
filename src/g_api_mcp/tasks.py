"""
Google Tasks MCP tools.

Tools
-----
tasks_list_tasklists  — all task lists for the account
tasks_list_tasks      — thin task summaries for a list
tasks_get_task        — full task details for one task ID
tasks_create_task     — create a new task
tasks_update_task     — partial update an existing task
tasks_complete_task   — convenience wrapper to mark a task done
tasks_delete_task     — permanently delete a task
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
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


async def _tasks_service():
    creds = await asyncio.to_thread(cred_manager.get_valid_credentials)
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def _http_error_message(e: HttpError) -> str:
    try:
        detail = json.loads(e.content.decode()).get("error", {}).get("message", str(e))
    except Exception:
        detail = str(e)
    return f"Google API error {e.resp.status}: {detail}"


def _to_thin_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "title": task.get("title", "(untitled)"),
        "status": task.get("status", "needsAction"),
        "due": task.get("due"),
        "updated": task.get("updated"),
        "has_notes": bool(task.get("notes")),
        "has_parent": bool(task.get("parent")),
        "has_links": bool(task.get("links")),
        "position": task.get("position"),
    }


def _to_full_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "title": task.get("title", "(untitled)"),
        "status": task.get("status", "needsAction"),
        "notes": task.get("notes"),
        "due": task.get("due"),
        "completed": task.get("completed"),
        "updated": task.get("updated"),
        "parent": task.get("parent"),
        "position": task.get("position"),
        "links": task.get("links", []),
        "hidden": task.get("hidden", False),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def tasks_list_tasklists() -> str:
    """List all Google Task lists for this account.
    Returns list IDs needed for other tasks_* tools.
    The default task list has ID "@default".
    """
    try:
        service = await _tasks_service()
        result = await asyncio.to_thread(
            lambda: service.tasklists().list(maxResults=100).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    lists = [
        {
            "id": tl["id"],
            "title": tl.get("title", ""),
            "updated": tl.get("updated"),
        }
        for tl in result.get("items", [])
    ]
    env = build_envelope(data=lists, result_count=len(lists), has_more=False)
    return json.dumps(env)


@mcp.tool()
async def tasks_list_tasks(
    tasklist_id: str = "@default",
    show_completed: bool = False,
    due_min: str | None = None,
    due_max: str | None = None,
    max_results: int = 20,
    page_cursor: str | None = None,
) -> str:
    """List tasks in a Google Task list. Returns thin summaries (no notes).
    Use tasks_get_task to read notes and links for specific tasks.

    Args:
        tasklist_id: Task list ID. Use "@default" for the default list, or an ID
                     from tasks_list_tasklists.
        show_completed: Include completed tasks. Default false.
        due_min: RFC 3339 — only tasks due on or after this datetime.
        due_max: RFC 3339 — only tasks due before this datetime.
        max_results: Tasks per page (1–100). Default 20.
        page_cursor: Pass pagination.next_cursor from a prior call to get the next page.
    """
    max_results = max(1, min(max_results, 100))

    try:
        service = await _tasks_service()
        kwargs: dict[str, Any] = {
            "tasklist": tasklist_id,
            "maxResults": max_results,
            "showCompleted": show_completed,
            "showHidden": False,
            "showDeleted": False,
        }
        if due_min:
            kwargs["dueMin"] = due_min
        if due_max:
            kwargs["dueMax"] = due_max
        if page_cursor:
            kwargs["pageToken"] = page_cursor

        result = await asyncio.to_thread(
            lambda: service.tasks().list(**kwargs).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    thin_tasks = [_to_thin_task(t) for t in result.get("items", [])]
    env = build_envelope(
        data=thin_tasks,
        has_more="nextPageToken" in result,
        next_cursor=result.get("nextPageToken"),
    )
    return json.dumps(env)


@mcp.tool()
async def tasks_get_task(
    task_id: str,
    tasklist_id: str = "@default",
) -> str:
    """Fetch full details for a single task, including notes, links, and parent info.

    Args:
        task_id: Task ID from a tasks_list_tasks response.
        tasklist_id: Task list that owns this task. Default "@default".
    """
    try:
        service = await _tasks_service()
        task = await asyncio.to_thread(
            lambda: service.tasks().get(
                tasklist=tasklist_id, task=task_id
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data=_to_full_task(task), is_list=False)
    return json.dumps(env)


@mcp.tool()
async def tasks_create_task(
    title: str,
    tasklist_id: str = "@default",
    notes: str | None = None,
    due: str | None = None,
    parent_task_id: str | None = None,
) -> str:
    """Create a new task.

    Args:
        title: Task title.
        tasklist_id: Target task list. Default "@default".
        notes: Task description/notes.
        due: Due date in RFC 3339 format. The time portion is ignored by the
             Google Tasks API — only the date matters (e.g. "2026-04-10T00:00:00.000Z").
        parent_task_id: Make this a subtask of an existing task.
    """
    task_body: dict[str, Any] = {
        "title": title,
        "status": "needsAction",
    }
    if notes:
        task_body["notes"] = notes
    if due:
        task_body["due"] = due

    try:
        service = await _tasks_service()
        kwargs: dict[str, Any] = {"tasklist": tasklist_id, "body": task_body}
        if parent_task_id:
            kwargs["parent"] = parent_task_id

        result = await asyncio.to_thread(
            lambda: service.tasks().insert(**kwargs).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={
            "task_id": result["id"],
            "title": result.get("title"),
            "status": result.get("status"),
            "due": result.get("due"),
        },
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def tasks_update_task(
    task_id: str,
    tasklist_id: str = "@default",
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
    status: str | None = None,
) -> str:
    """Partially update an existing task (PATCH semantics — only supplied fields change).

    Args:
        task_id: Task ID to update.
        tasklist_id: Task list that owns the task. Default "@default".
        title: New title (omit to keep current).
        notes: New notes (omit to keep current).
        due: New due date in RFC 3339 format (omit to keep current).
        status: "needsAction" or "completed" (omit to keep current).
    """
    patch: dict[str, Any] = {}
    if title is not None:
        patch["title"] = title
    if notes is not None:
        patch["notes"] = notes
    if due is not None:
        patch["due"] = due
    if status is not None:
        if status not in ("needsAction", "completed"):
            raise ToolError('status must be "needsAction" or "completed".')
        patch["status"] = status
        if status == "completed" and "completed" not in patch:
            patch["completed"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        elif status == "needsAction":
            patch["completed"] = None  # clears the completed timestamp

    if not patch:
        raise ToolError(
            "No fields to update — provide at least one of: title, notes, due, status."
        )

    try:
        service = await _tasks_service()
        result = await asyncio.to_thread(
            lambda: service.tasks().patch(
                tasklist=tasklist_id, task=task_id, body=patch
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={"task_id": result["id"], "updated": result.get("updated")},
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def tasks_complete_task(
    task_id: str,
    tasklist_id: str = "@default",
) -> str:
    """Mark a task as completed. Sets status to "completed" and stamps the completion timestamp.

    Args:
        task_id: Task ID to complete.
        tasklist_id: Task list that owns the task. Default "@default".
    """
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        service = await _tasks_service()
        result = await asyncio.to_thread(
            lambda: service.tasks().patch(
                tasklist=tasklist_id,
                task=task_id,
                body={"status": "completed", "completed": completed_at},
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={
            "task_id": result["id"],
            "status": result.get("status"),
            "completed": result.get("completed"),
        },
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def tasks_delete_task(
    task_id: str,
    tasklist_id: str = "@default",
) -> str:
    """Permanently delete a task. This cannot be undone.

    Args:
        task_id: Task ID to delete.
        tasklist_id: Task list that owns the task. Default "@default".
    """
    try:
        service = await _tasks_service()
        await asyncio.to_thread(
            lambda: service.tasks().delete(
                tasklist=tasklist_id, task=task_id
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data={"deleted": True}, is_list=False)
    return json.dumps(env)
