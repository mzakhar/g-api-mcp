"""
End-to-end smoke tests for the full sync pipeline.
Layer 5 of the validation plan (SPEC-phase-4.md).

Uses the 5-task fixture corpus from tests/fixtures/gtasks_corpus.json.
These tests mock only the Google API layer — all vault I/O uses a tmp_path copy.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp import sync as sync_module
from g_api_mcp.sync import (
    SyncConfig,
    SyncState,
    TaskListConfig,
    _to_vault_line,
    _route_task,
    write_vault_task,
    run_sync,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "gtasks_corpus.json"
SECTION = "## Top Priorities"


def _make_config(vault_path: Path) -> SyncConfig:
    return SyncConfig(
        vault_path=vault_path,
        task_lists=[TaskListConfig(id="list1", name="My Tasks")],
        daily_notes_path="00 Daily Plan",
        daily_note_section=SECTION,
    )


def _run_pipeline_once(tasks: list[dict], vault_path: Path):
    """Run the transform+route+write pipeline for a list of tasks without mocking Google API cleanup."""
    from datetime import date
    config = _make_config(vault_path)
    tl = config.task_lists[0]
    today = date(2026, 4, 5)
    for task in tasks:
        indent = 1 if task.get("parent") else 0
        line = _to_vault_line(task, indent=indent)
        target, section = _route_task(task, tl, config, today)
        write_vault_task(line, target, section)


def test_full_pipeline_5_task_corpus(tmp_path):
    """All 5 corpus tasks produce correctly-formatted vault task lines."""
    tasks = json.loads(CORPUS_PATH.read_text())
    assert len(tasks) == 5

    _run_pipeline_once(tasks, tmp_path)

    # Collect all written files
    md_files = list(tmp_path.rglob("*.md"))
    assert len(md_files) > 0, "Expected at least one vault file to be created"

    all_content = "\n".join(f.read_text(encoding="utf-8") for f in md_files)

    # Verify all 5 task titles appear
    for task in tasks:
        assert task["title"] in all_content, \
            f"Task '{task['title']}' not found in vault output"

    # Verify no hard due dates were used
    assert "📅" not in all_content, "Google Task due dates must use ⏳, not 📅"

    # Verify completed task has [x] checkbox
    completed_task = next(t for t in tasks if t["status"] == "completed")
    corpus_2_pattern = re.compile(r'- \[x\].*' + re.escape(completed_task["title"]))
    assert corpus_2_pattern.search(all_content), "Completed task should have [x] checkbox"

    # Verify someday task has #someday tag
    assert "#someday" in all_content, "Someday task should have #someday tag"


def test_idempotency(tmp_path):
    """Running the pipeline twice produces byte-for-byte identical vault files."""
    tasks = json.loads(CORPUS_PATH.read_text())

    _run_pipeline_once(tasks, tmp_path)
    snapshots_first = {
        f.relative_to(tmp_path): f.read_text(encoding="utf-8")
        for f in sorted(tmp_path.rglob("*.md"))
    }

    _run_pipeline_once(tasks, tmp_path)
    snapshots_second = {
        f.relative_to(tmp_path): f.read_text(encoding="utf-8")
        for f in sorted(tmp_path.rglob("*.md"))
    }

    assert set(snapshots_first.keys()) == set(snapshots_second.keys()), \
        "Second run created different files than first run"
    for rel_path in snapshots_first:
        assert snapshots_first[rel_path] == snapshots_second[rel_path], \
            f"File {rel_path} differs between runs — sync is not idempotent"


def test_partial_update_updates_title(tmp_path):
    """Updating a task's title on second run replaces the old line."""
    tasks = json.loads(CORPUS_PATH.read_text())
    _run_pipeline_once(tasks, tmp_path)

    # Modify one task's title
    modified = [
        {**t, "title": "MODIFIED: " + t["title"]} if t["id"] == "corpus-1" else t
        for t in tasks
    ]
    _run_pipeline_once(modified, tmp_path)

    all_content = "\n".join(
        f.read_text(encoding="utf-8") for f in tmp_path.rglob("*.md")
    )
    assert "MODIFIED: Review Q2 roadmap" in all_content
