"""
Integration tests for API wiring — tasklist ID forwarding and error handling.
Layer 4 of the validation plan (SPEC-phase-4.md).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from googleapiclient.errors import HttpError

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp import sync as sync_module
from g_api_mcp.sync import fetch_changed_tasks, SyncConfig, SyncState, TaskListConfig, run_sync


def _make_config(vault_path: Path) -> SyncConfig:
    return SyncConfig(
        vault_path=vault_path,
        task_lists=[TaskListConfig(id="my-list-id", name="My Tasks")],
    )


# ---------------------------------------------------------------------------
# Layer 4: API wiring integration tests
# ---------------------------------------------------------------------------


def test_tasklist_id_forwarded_correctly():
    service = MagicMock()
    service.tasks().list().execute.return_value = {"items": []}

    fetch_changed_tasks(service, "my-list-id", None)

    call_kwargs = service.tasks().list.call_args.kwargs
    assert call_kwargs["tasklist"] == "my-list-id"


def test_api_error_on_list_produces_no_vault_write(tmp_path):
    """If Google Tasks list call fails, vault should be untouched."""
    config = _make_config(tmp_path)
    state = SyncState()

    # Make the service raise on tasks().list().execute()
    http_resp = MagicMock()
    http_resp.status = 403
    error = HttpError(resp=http_resp, content=b'{"error": {"message": "Forbidden"}}')

    service = MagicMock()
    service.tasks().list().execute.side_effect = error

    with patch.object(sync_module, "cred_manager") as mock_cm:
        mock_cm.get_valid_credentials.return_value = MagicMock()
        with patch("g_api_mcp.sync.build", return_value=service):
            new_state, summary = run_sync(config, state)

    # Vault directory should have no files written
    vault_files = list(tmp_path.rglob("*.md"))
    assert vault_files == [], f"Expected no vault files, got: {vault_files}"
    assert summary["errors"] > 0


def test_already_processed_task_is_skipped(tmp_path):
    """Task with same updated timestamp as in state should be skipped."""
    from g_api_mcp.sync import ProcessedTask

    config = _make_config(tmp_path)
    state = SyncState(
        last_sync={"my-list-id": "2026-04-05T00:00:00Z"},
        processed_tasks={
            "t1": ProcessedTask(
                updated="2026-04-05T09:00:00.000Z",
                vault_path="00 Daily Plan/2026-04-10.md",
            )
        },
    )

    service = MagicMock()
    service.tasks().list().execute.return_value = {
        "items": [
            {
                "id": "t1",
                "title": "Already done",
                "status": "needsAction",
                "due": "2026-04-10T00:00:00.000Z",
                "updated": "2026-04-05T09:00:00.000Z",  # same as in state
            }
        ]
    }

    with patch.object(sync_module, "cred_manager") as mock_cm:
        mock_cm.get_valid_credentials.return_value = MagicMock()
        with patch("g_api_mcp.sync.build", return_value=service):
            new_state, summary = run_sync(config, state)

    assert summary["skipped"] == 1
    assert summary["processed"] == 0
    vault_files = list(tmp_path.rglob("*.md"))
    assert vault_files == []
