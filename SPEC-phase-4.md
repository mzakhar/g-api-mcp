---
topic: Google Tasks to Obsidian Vault Sync
phase: 4
depends_on: [phase-1, phase-2, phase-3]
date: 2026-04-05
---

# SPEC Phase 4: Test Harness + Validation

## Objective

Build the empirical validation harness: 17 tests across 5 layers, an initial human-reviewed
corpus baseline, and an end-to-end smoke test that validates the full pipeline with real
(or fixture) Google Tasks data.

## Inputs required

- All prior phases complete and passing basic manual validation
- `tests/test_tasks.py` (existing test patterns to follow)
- `pytest`, `pytest-asyncio` already in dev dependencies

## Deliverables

- `tests/test_sync_transform.py` — Layer 1+2: transformation + change detection unit tests
- `tests/test_sync_vault.py` — Layer 3: vault write + cleanup unit tests (tmp_path fixture)
- `tests/test_sync_integration.py` — Layer 4: API wiring integration tests
- `tests/test_sync_e2e.py` — Layer 5: end-to-end smoke test with 5-task fixture corpus
- `tests/fixtures/gtasks_corpus.json` — 5-task fixture corpus (human-reviewed baselines)
- `tests/fixtures/expected_vault_output.md` — expected vault output for corpus (human-reviewed)

## Boundaries

Always:
- Follow existing mock pattern: `patch.object(sync_module, "_tasks_service", ...)`
- Use `tmp_path` pytest fixture for all vault file I/O in tests — never write to real vault
- Test each layer independently — no cross-layer dependencies within a single test

Ask first:
- Adding new test dependencies (e.g., pytest-snapshot)

Never:
- Run integration or e2e tests against production Google Tasks without explicit opt-in flag
- Commit `tests/fixtures/expected_vault_output.md` without human review of its contents

## Test Plan (17 tests)

### Layer 1 — Unit: `_to_vault_line()` transformation (tests 1–7)
File: `tests/test_sync_transform.py`

| # | Test name | Input | Expected |
|---|---|---|---|
| 1 | `test_basic_incomplete_task` | `{id: "a1", title: "Review PR", status: "needsAction", due: "2026-04-10T00:00:00.000Z"}` | `- [ ] 🔼 Review PR ⏳ 2026-04-10 <!-- gtask:a1 -->` |
| 2 | `test_completed_task` | `{id: "a2", status: "completed", title: "Done", completed: "2026-04-05T15:30:00Z"}` | `- [x] 🔼 Done ✅ 2026-04-05 <!-- gtask:a2 -->` |
| 3 | `test_task_no_due_date` | `{id: "a3", title: "Float task", status: "needsAction"}` | No `⏳` or `📅` in output |
| 4 | `test_deleted_task` | `{id: "a4", title: "Old task", deleted: True}` | `- [-] 🔼 Old task <!-- gtask:a4 -->` |
| 5 | `test_task_with_short_notes` | `{id: "a5", title: "Call Mike", notes: "re: budget"}` | `- [ ] 🔼 Call Mike — re: budget <!-- gtask:a5 -->` |
| 6 | `test_task_with_long_notes` | `{id: "a6", title: "Research", notes: "A" * 90}` | Notes rendered as sub-bullet line, not inline |
| 7 | `test_untitled_task` | `{id: "a7", title: ""}` | Title renders as `(untitled)`, no crash |

**Field assertion strategy** — use regex, not exact string match:
```python
import re

def parse_task_line(line: str) -> dict:
    return {
        "checkbox": re.search(r'- \[(.)\]', line).group(1),
        "priority": re.search(r'(⏫|🔼|🔽)', line).group(1) if re.search(r'(⏫|🔼|🔽)', line) else None,
        "scheduled": re.search(r'⏳ (\d{4}-\d{2}-\d{2})', line),
        "due": re.search(r'📅 (\d{4}-\d{2}-\d{2})', line),
        "completed": re.search(r'✅ (\d{4}-\d{2}-\d{2})', line),
        "task_id": re.search(r'<!-- gtask:(\S+?) -->', line).group(1),
    }
```

### Layer 2 — Unit: Change detection (tests 8–10)
File: `tests/test_sync_transform.py`

| # | Test name | Setup | Assert |
|---|---|---|---|
| 8 | `test_updated_min_passed_to_api` | Mock service, call `fetch_changed_tasks("list1", "2026-04-05T00:00:00Z")` | `service.tasks().list.call_args.kwargs["updatedMin"] == "2026-04-05T00:00:00Z"` |
| 9 | `test_no_tasks_when_updated_min_future` | Mock returns empty `items` list | `fetch_changed_tasks(...)` returns `[]` |
| 10 | `test_show_deleted_hidden_completed_flags` | Mock service | Assert `showDeleted=True`, `showHidden=True`, `showCompleted=True` in call_args |

### Layer 3 — Unit: Vault write + cleanup (tests 11–13)
File: `tests/test_sync_vault.py` — use `tmp_path` fixture for all file I/O

| # | Test name | Setup | Assert |
|---|---|---|---|
| 11 | `test_new_task_inserted_in_section` | Empty tmp vault dir, call `write_vault_task` | Task line appears under correct section header |
| 12 | `test_duplicate_task_not_inserted` | Write same task twice | File contains exactly one line with that `<!-- gtask:{id} -->` |
| 13 | `test_updated_task_line_replaced` | Write task, then write same ID with updated title | Only new title present, no stale line |

### Layer 4 — Integration: API wiring (tests 14–15)
File: `tests/test_sync_integration.py`

| # | Test name | Setup | Assert |
|---|---|---|---|
| 14 | `test_tasklist_id_forwarded` | Mock service, call `fetch_changed_tasks("my-list-id", None)` | `service.tasks().list.call_args.kwargs["tasklist"] == "my-list-id"` |
| 15 | `test_api_error_produces_no_vault_write` | Mock raises `HttpError(403)`, call `run_sync` with tmp vault | tmp vault dir is empty after run |

### Layer 5 — End-to-end (tests 16–17)
File: `tests/test_sync_e2e.py`

| # | Test name | Corpus | Assert |
|---|---|---|---|
| 16 | `test_full_pipeline_5_task_corpus` | Load `fixtures/gtasks_corpus.json` (5 tasks), run against tmp vault | All 5 expected lines present in output file; diff against `fixtures/expected_vault_output.md` |
| 17 | `test_idempotency` | Run full pipeline twice with same corpus | Output file byte-for-byte identical after both runs |

## Corpus baseline (human-reviewed — do not automate)

`tests/fixtures/gtasks_corpus.json` must cover:
1. Basic incomplete task with due date
2. Completed task with completion timestamp
3. Task with no due date (→ `#someday`)
4. Subtask (has `parent` field)
5. Task with long notes (multi-line sub-bullets)

After generating `tests/fixtures/expected_vault_output.md` from the first test run, **review
it manually** against the vault conventions in `30 Library/Task Management System.md` before
committing it as the baseline. This is the one mandatory human step in the validation pipeline.

## Validation criteria

- [ ] `pytest tests/test_sync_transform.py` — all 10 tests pass
- [ ] `pytest tests/test_sync_vault.py` — all 3 tests pass
- [ ] `pytest tests/test_sync_integration.py` — all 2 tests pass
- [ ] `pytest tests/test_sync_e2e.py` — test 17 (idempotency) passes
- [ ] Test 16 (corpus baseline) passes after human review of `expected_vault_output.md`
- [ ] `pytest --co` shows no collection errors across all test files
- [ ] Human review: `expected_vault_output.md` matches vault conventions (checkbox, ⏳ not 📅, ID comment, priority emoji, section placement)

## Code patterns / examples

```python
# tests/test_sync_transform.py — Layer 1 example
import pytest
from g_api_mcp.sync import _to_vault_line

def parse_task_line(line):
    import re
    first_line = line.splitlines()[0]
    return {
        "checkbox": re.search(r'- \[(.)\]', first_line).group(1),
        "priority": (re.search(r'(⏫|🔼|🔽)', first_line) or type('', (), {'group': lambda s, n: None})()).group(1),
        "scheduled": re.search(r'⏳ (\d{4}-\d{2}-\d{2})', first_line),
        "due_hard": re.search(r'📅 (\d{4}-\d{2}-\d{2})', first_line),
        "completed": re.search(r'✅ (\d{4}-\d{2}-\d{2})', first_line),
        "task_id": re.search(r'<!-- gtask:(\S+?) -->', first_line).group(1),
    }

def test_basic_incomplete_task():
    task = {
        "id": "a1",
        "title": "Review PR",
        "status": "needsAction",
        "due": "2026-04-10T00:00:00.000Z",
    }
    result = parse_task_line(_to_vault_line(task))
    assert result["checkbox"] == " "
    assert result["priority"] == "🔼"
    assert result["scheduled"].group(1) == "2026-04-10"
    assert result["due_hard"] is None  # never 📅 for Google Tasks
    assert result["task_id"] == "a1"

def test_no_due_date_never_emits_hard_due():
    """Google Task due dates must NEVER produce 📅 — would corrupt Overdue dashboard."""
    task = {"id": "b1", "title": "Something", "status": "needsAction",
            "due": "2026-04-15T00:00:00.000Z"}
    line = _to_vault_line(task)
    assert "📅" not in line, "Google Task due dates must use ⏳, not 📅"
```

```python
# tests/test_sync_e2e.py — idempotency test
import json
from pathlib import Path
import pytest
from g_api_mcp.sync import _to_vault_line, _route_task, write_vault_task

CORPUS_PATH = Path(__file__).parent / "fixtures" / "gtasks_corpus.json"

def test_idempotency(tmp_path):
    tasks = json.loads(CORPUS_PATH.read_text())
    config = _make_test_config(vault_path=tmp_path)

    def run_once():
        for task in tasks:
            line = _to_vault_line(task)
            target, section = _route_task(task, config.task_lists[0], config,
                                           date(2026, 4, 5))
            write_vault_task(line, target, section)

    run_once()
    snapshot_after_first = (tmp_path / "00 Daily Plan" / "2026-04-05.md").read_text()

    run_once()
    snapshot_after_second = (tmp_path / "00 Daily Plan" / "2026-04-05.md").read_text()

    assert snapshot_after_first == snapshot_after_second, \
        "Sync is not idempotent — running twice produces different output"
```
