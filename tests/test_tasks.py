"""
Tests for Tasks tool handlers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp import tasks as tasks_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_task(
    task_id: str = "task1",
    title: str = "Review PR",
    status: str = "needsAction",
    due: str | None = "2026-04-10T00:00:00.000Z",
    notes: str | None = None,
    parent: str | None = None,
    links: list | None = None,
    completed: str | None = None,
) -> dict:
    task: dict = {
        "id": task_id,
        "title": title,
        "status": status,
        "updated": "2026-04-05T10:00:00.000Z",
        "position": "00000000000000000001",
        "hidden": False,
    }
    if due:
        task["due"] = due
    if notes:
        task["notes"] = notes
    if parent:
        task["parent"] = parent
    if links:
        task["links"] = links
    if completed:
        task["completed"] = completed
    return task


# ---------------------------------------------------------------------------
# tasks_list_tasklists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasklists():
    service = MagicMock()
    service.tasklists().list().execute.return_value = {
        "items": [
            {"id": "@default", "title": "My Tasks", "updated": "2026-04-01T00:00:00.000Z"},
            {"id": "list2", "title": "Work", "updated": "2026-04-02T00:00:00.000Z"},
        ]
    }

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_list_tasklists()

    env = json.loads(result)
    assert env["success"] is True
    assert len(env["data"]) == 2
    assert env["data"][0]["id"] == "@default"
    assert env["pagination"]["has_more"] is False


# ---------------------------------------------------------------------------
# tasks_list_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_thin_summaries():
    tasks = [
        make_task("t1", "Review PR", notes="Check auth module"),
        make_task("t2", "Write tests", status="completed"),
    ]
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": tasks}

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_list_tasks()

    env = json.loads(result)
    assert env["success"] is True
    assert len(env["data"]) == 2
    # Thin — no notes in output
    for t in env["data"]:
        assert "notes" not in t
        assert "id" in t
        assert "title" in t
        assert "status" in t
        assert "has_notes" in t

    # has_notes flag correctly set
    assert env["data"][0]["has_notes"] is True
    assert env["data"][1]["has_notes"] is False


@pytest.mark.asyncio
async def test_list_tasks_pagination():
    service = MagicMock()
    service.tasks().list().execute.return_value = {
        "items": [make_task()],
        "nextPageToken": "page2",
    }

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_list_tasks()

    env = json.loads(result)
    assert env["pagination"]["has_more"] is True
    assert env["pagination"]["next_cursor"] == "page2"


@pytest.mark.asyncio
async def test_list_tasks_max_results_capped():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await tasks_module.tasks_list_tasks(max_results=9999)

    call_kwargs = service.tasks().list.call_args.kwargs
    assert call_kwargs["maxResults"] <= 100


@pytest.mark.asyncio
async def test_list_tasks_show_completed_false_by_default():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await tasks_module.tasks_list_tasks()

    call_kwargs = service.tasks().list.call_args.kwargs
    assert call_kwargs["showCompleted"] is False


# ---------------------------------------------------------------------------
# tasks_get_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_full_fields():
    task = make_task(
        "t1",
        notes="Important context",
        parent="parent1",
        links=[{"type": "email", "description": "Thread", "link": "https://mail.google.com/..."}],
    )
    service = MagicMock()
    service.tasks().get().execute.return_value = task

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_get_task(task_id="t1")

    env = json.loads(result)
    assert env["success"] is True
    assert env["pagination"] is None
    assert env["data"]["notes"] == "Important context"
    assert env["data"]["parent"] == "parent1"
    assert len(env["data"]["links"]) == 1


# ---------------------------------------------------------------------------
# tasks_create_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_basic():
    service = MagicMock()
    service.tasks().insert().execute.return_value = make_task("new1", title="New Task")

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_create_task(title="New Task")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["task_id"] == "new1"
    assert env["data"]["status"] == "needsAction"


@pytest.mark.asyncio
async def test_create_task_with_subtask():
    service = MagicMock()
    service.tasks().insert().execute.return_value = make_task("sub1", parent="parent1")

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await tasks_module.tasks_create_task(
            title="Subtask",
            parent_task_id="parent1",
        )

    call_kwargs = service.tasks().insert.call_args.kwargs
    assert call_kwargs["parent"] == "parent1"


# ---------------------------------------------------------------------------
# tasks_update_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_title():
    service = MagicMock()
    service.tasks().patch().execute.return_value = {
        "id": "t1",
        "updated": "2026-04-05T12:00:00.000Z",
    }

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_update_task(task_id="t1", title="New Title")

    env = json.loads(result)
    assert env["success"] is True
    patch_body = service.tasks().patch.call_args.kwargs["body"]
    assert patch_body["title"] == "New Title"
    assert "notes" not in patch_body


@pytest.mark.asyncio
async def test_update_task_invalid_status():
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError):
        await tasks_module.tasks_update_task(task_id="t1", status="invalid")


@pytest.mark.asyncio
async def test_update_task_no_fields_raises():
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError):
        await tasks_module.tasks_update_task(task_id="t1")


@pytest.mark.asyncio
async def test_update_task_complete_stamps_timestamp():
    service = MagicMock()
    service.tasks().patch().execute.return_value = {
        "id": "t1",
        "updated": "2026-04-05T12:00:00.000Z",
    }

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await tasks_module.tasks_update_task(task_id="t1", status="completed")

    patch_body = service.tasks().patch.call_args.kwargs["body"]
    assert patch_body["status"] == "completed"
    assert "completed" in patch_body


@pytest.mark.asyncio
async def test_update_task_reopen_clears_completed():
    service = MagicMock()
    service.tasks().patch().execute.return_value = {
        "id": "t1",
        "updated": "2026-04-05T12:00:00.000Z",
    }

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await tasks_module.tasks_update_task(task_id="t1", status="needsAction")

    patch_body = service.tasks().patch.call_args.kwargs["body"]
    assert patch_body["status"] == "needsAction"
    assert patch_body["completed"] is None


# ---------------------------------------------------------------------------
# tasks_complete_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task():
    service = MagicMock()
    service.tasks().patch().execute.return_value = make_task(
        "t1", status="completed", completed="2026-04-05T15:30:00.000Z"
    )

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_complete_task(task_id="t1")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["status"] == "completed"
    assert env["data"]["completed"] is not None

    patch_body = service.tasks().patch.call_args.kwargs["body"]
    assert patch_body["status"] == "completed"
    assert "completed" in patch_body


# ---------------------------------------------------------------------------
# tasks_delete_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task():
    service = MagicMock()
    service.tasks().delete().execute.return_value = None  # 204 No Content

    with patch.object(tasks_module, "_tasks_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await tasks_module.tasks_delete_task(task_id="t1")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["deleted"] is True


# ---------------------------------------------------------------------------
# _to_thin_task / _to_full_task helpers
# ---------------------------------------------------------------------------


def test_to_thin_task_strips_notes():
    task = make_task("t1", notes="Private notes")
    thin = tasks_module._to_thin_task(task)
    assert "notes" not in thin
    assert thin["has_notes"] is True
    assert "id" in thin
    assert "title" in thin


def test_to_thin_task_no_notes():
    task = make_task("t1")
    thin = tasks_module._to_thin_task(task)
    assert thin["has_notes"] is False


def test_to_thin_task_has_parent():
    task = make_task("t1", parent="parent_id")
    thin = tasks_module._to_thin_task(task)
    assert thin["has_parent"] is True


def test_to_full_task_includes_notes():
    task = make_task("t1", notes="Do this carefully", links=[{"type": "email"}])
    full = tasks_module._to_full_task(task)
    assert full["notes"] == "Do this carefully"
    assert len(full["links"]) == 1
