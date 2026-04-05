"""
Tests for sync module transformation and change detection logic.
Layers 1 and 2 of the validation plan (SPEC-phase-4.md).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp import sync as sync_module
from g_api_mcp.sync import _to_vault_line, fetch_changed_tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_task_line(line: str) -> dict:
    """Parse the first line of a vault task into its components."""
    first = line.splitlines()[0]
    m_checkbox = re.search(r'- \[(.)\]', first)
    m_priority = re.search(r'(⏫|🔼|🔽)', first)
    m_scheduled = re.search(r'⏳ (\d{4}-\d{2}-\d{2})', first)
    m_due_hard = re.search(r'📅 (\d{4}-\d{2}-\d{2})', first)
    m_completed = re.search(r'✅ (\d{4}-\d{2}-\d{2})', first)
    m_task_id = re.search(r'<!-- gtask:(\S+?) -->', first)
    return {
        "checkbox": m_checkbox.group(1) if m_checkbox else None,
        "priority": m_priority.group(1) if m_priority else None,
        "scheduled": m_scheduled.group(1) if m_scheduled else None,
        "due_hard": m_due_hard.group(1) if m_due_hard else None,
        "completed": m_completed.group(1) if m_completed else None,
        "task_id": m_task_id.group(1) if m_task_id else None,
        "raw": first,
    }


# ---------------------------------------------------------------------------
# Layer 1: _to_vault_line transformation unit tests
# ---------------------------------------------------------------------------


def test_basic_incomplete_task():
    task = {
        "id": "a1",
        "title": "Review PR",
        "status": "needsAction",
        "due": "2026-04-10T00:00:00.000Z",
    }
    parsed = parse_task_line(_to_vault_line(task))
    assert parsed["checkbox"] == " ", "open task should have space checkbox"
    assert parsed["priority"] == "🔼"
    assert parsed["scheduled"] == "2026-04-10"
    assert parsed["task_id"] == "a1"


def test_completed_task():
    task = {
        "id": "a2",
        "title": "Done task",
        "status": "completed",
        "completed": "2026-04-05T15:30:00.000Z",
        "due": "2026-04-05T00:00:00.000Z",
    }
    parsed = parse_task_line(_to_vault_line(task))
    assert parsed["checkbox"] == "x"
    assert parsed["completed"] == "2026-04-05"
    assert parsed["task_id"] == "a2"


def test_task_no_due_date_has_no_date_marker():
    task = {"id": "a3", "title": "Float task", "status": "needsAction"}
    line = _to_vault_line(task)
    parsed = parse_task_line(line)
    assert parsed["scheduled"] is None
    assert parsed["due_hard"] is None


def test_deleted_task():
    task = {"id": "a4", "title": "Old task", "deleted": True}
    parsed = parse_task_line(_to_vault_line(task))
    assert parsed["checkbox"] == "-"
    assert parsed["task_id"] == "a4"


def test_task_with_short_notes_inline():
    task = {
        "id": "a5",
        "title": "Call Mike",
        "status": "needsAction",
        "notes": "re: budget",
    }
    line = _to_vault_line(task)
    first_line = line.splitlines()[0]
    assert "re: budget" in first_line, "short notes should be inline"
    assert len(line.splitlines()) == 1, "short notes should not add sub-bullets"


def test_task_with_long_notes_as_sub_bullet():
    long_notes = "A" * 90
    task = {
        "id": "a6",
        "title": "Research",
        "status": "needsAction",
        "notes": long_notes,
    }
    lines = _to_vault_line(task).splitlines()
    assert len(lines) > 1, "long notes should produce sub-bullets"
    assert long_notes not in lines[0], "long notes should not be inline"


def test_untitled_task_fallback():
    task = {"id": "a7", "title": "", "status": "needsAction"}
    line = _to_vault_line(task)
    assert "(untitled)" in line


def test_someday_keyword_gets_someday_tag_and_low_priority():
    task = {"id": "s1", "title": "Someday: explore AI tools", "status": "needsAction"}
    line = _to_vault_line(task)
    assert "#someday" in line
    parsed = parse_task_line(line)
    assert parsed["priority"] == "🔽"


def test_google_task_due_date_never_emits_hard_due_emoji():
    """Critical: Google Task due dates must use ⏳, never 📅 — 📅 corrupts Overdue dashboard."""
    task = {
        "id": "b1",
        "title": "Something important",
        "status": "needsAction",
        "due": "2026-04-15T00:00:00.000Z",
    }
    line = _to_vault_line(task)
    assert "📅" not in line, "Google Task due dates must use ⏳, not 📅"
    assert "⏳" in line


def test_task_id_always_present_in_comment():
    task = {"id": "myid123", "title": "Test", "status": "needsAction"}
    line = _to_vault_line(task)
    assert "<!-- gtask:myid123 -->" in line


def test_subtask_indented():
    task = {
        "id": "sub1",
        "title": "Subtask",
        "status": "needsAction",
        "parent": "parent1",
    }
    line = _to_vault_line(task, indent=1)
    assert line.startswith("  "), "subtask should be indented with 2 spaces"


# ---------------------------------------------------------------------------
# Layer 2: fetch_changed_tasks / change detection
# ---------------------------------------------------------------------------


def test_updated_min_passed_to_api():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    fetch_changed_tasks(service, "list1", "2026-04-05T00:00:00Z")

    call_kwargs = service.tasks().list.call_args.kwargs
    assert call_kwargs["updatedMin"] == "2026-04-05T00:00:00Z"


def test_no_updated_min_when_none():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    fetch_changed_tasks(service, "list1", None)

    call_kwargs = service.tasks().list.call_args.kwargs
    assert "updatedMin" not in call_kwargs


def test_show_deleted_hidden_completed_flags_always_set():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    fetch_changed_tasks(service, "list1", None)

    kw = service.tasks().list.call_args.kwargs
    assert kw["showDeleted"] is True
    assert kw["showHidden"] is True
    assert kw["showCompleted"] is True


def test_empty_list_returns_empty():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    result = fetch_changed_tasks(service, "list1", "2099-01-01T00:00:00Z")
    assert result == []


def test_pagination_collects_all_pages():
    service = MagicMock()
    # First call returns page1 + nextPageToken, second returns page2
    service.tasks().list().execute.side_effect = [
        {"items": [{"id": "t1"}], "nextPageToken": "tok2"},
        {"items": [{"id": "t2"}]},
    ]

    result = fetch_changed_tasks(service, "list1", None)
    assert len(result) == 2
    assert result[0]["id"] == "t1"
    assert result[1]["id"] == "t2"
