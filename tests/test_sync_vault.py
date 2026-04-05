"""
Tests for vault write (upsert) logic.
Layer 3 of the validation plan (SPEC-phase-4.md).
Uses tmp_path — never touches the real vault.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp.sync import write_vault_task


SECTION = "## Top Priorities"


def make_line(task_id: str, title: str = "Test task", due: str = "2026-04-10") -> str:
    return f"- [ ] 🔼 {title} ⏳ {due} <!-- gtask:{task_id} -->"


# ---------------------------------------------------------------------------
# Layer 3: write_vault_task
# ---------------------------------------------------------------------------


def test_new_task_inserted_in_section(tmp_path):
    target = tmp_path / "2026-04-10.md"
    target.write_text(f"# 2026-04-10\n\n{SECTION}\n\n", encoding="utf-8")
    line = make_line("task1")

    result = write_vault_task(line, target, SECTION)

    assert result is True
    content = target.read_text(encoding="utf-8")
    assert "<!-- gtask:task1 -->" in content
    assert "Test task" in content


def test_new_task_creates_file_if_missing(tmp_path):
    target = tmp_path / "00 Daily Plan" / "2026-04-20.md"
    line = make_line("task2")

    result = write_vault_task(line, target, SECTION)

    assert result is True
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "<!-- gtask:task2 -->" in content


def test_duplicate_task_not_inserted_twice(tmp_path):
    target = tmp_path / "2026-04-10.md"
    target.write_text(f"# 2026-04-10\n\n{SECTION}\n\n", encoding="utf-8")
    line = make_line("task3")

    write_vault_task(line, target, SECTION)
    write_vault_task(line, target, SECTION)

    content = target.read_text(encoding="utf-8")
    count = content.count("<!-- gtask:task3 -->")
    assert count == 1, f"Expected exactly 1 occurrence, got {count}"


def test_updated_task_line_replaced(tmp_path):
    target = tmp_path / "2026-04-10.md"
    target.write_text(f"# 2026-04-10\n\n{SECTION}\n\n", encoding="utf-8")

    original = make_line("task4", title="Old title")
    write_vault_task(original, target, SECTION)

    updated = make_line("task4", title="New title")
    write_vault_task(updated, target, SECTION)

    content = target.read_text(encoding="utf-8")
    assert "New title" in content
    assert "Old title" not in content
    assert content.count("<!-- gtask:task4 -->") == 1


def test_section_created_if_missing(tmp_path):
    target = tmp_path / "2026-04-10.md"
    target.write_text("# 2026-04-10\n\n## Other Section\n\nsome content\n", encoding="utf-8")
    line = make_line("task5")

    result = write_vault_task(line, target, SECTION)

    assert result is True
    content = target.read_text(encoding="utf-8")
    assert SECTION in content
    assert "<!-- gtask:task5 -->" in content


def test_multiple_tasks_in_same_section(tmp_path):
    target = tmp_path / "2026-04-10.md"
    target.write_text(f"# 2026-04-10\n\n{SECTION}\n\n", encoding="utf-8")

    write_vault_task(make_line("taskA", "Task Alpha"), target, SECTION)
    write_vault_task(make_line("taskB", "Task Beta"), target, SECTION)

    content = target.read_text(encoding="utf-8")
    assert "<!-- gtask:taskA -->" in content
    assert "<!-- gtask:taskB -->" in content
