---
topic: Google Tasks to Obsidian Vault Sync
date: 2026-04-05
status: draft
sources: sources.json
---

# SPEC: Google Tasks → Obsidian Vault Sync

## Problem Statement

Google Tasks (populated by Gemini on mobile) needs to be synced into the Obsidian vault as
properly-formatted vault tasks. The system must detect task changes without webhooks (polling
only), transform Google Task fields into the vault's specific task syntax, route tasks to the
correct vault note, and clean up processed tasks in Google Tasks — all idempotently and with
empirical validation of output correctness.

## Background & Research Summary

**No push notifications exist for Google Tasks.** The Google Workspace Events API covers Chat,
Meet, and Drive but explicitly excludes Tasks. Polling via `tasks.list?updatedMin=<RFC3339>` is
the only supported change-detection mechanism. At 60-second intervals, this consumes ~1,440
API calls/day per task list — well within the 50,000/day quota. [Source: Google Tasks API
tasks.list reference; Google Workspace Events API docs]

**The vault has a precise, enforced task syntax.** The authoritative reference is
`30 Library/Task Management System.md`. The format is
`- [STATE] PRIORITY Description ⏳/📅 YYYY-MM-DD #tag`. The critical semantic distinction:
`⏳` (scheduled) is the default for all intent-based dates and is updated on carry-forward;
`📅` (due) marks hard external deadlines and is never updated. Using `📅` for Google Task due
dates would corrupt the Overdue dashboard. All Google Task due dates must map to `⏳`.
[Source: vault Task Management System.md, Tasks Dashboard queries]

**The existing g-api-mcp package is the right architectural home.** It is Python/FastMCP with
a `GoogleCredentialManager` singleton that works in both MCP server and CLI contexts. Adding a
`sync.py` module and `g-api-mcp-sync` console script reuses auth, Google API patterns, and test
infrastructure with zero new plumbing. The primary trigger is Windows Task Scheduler (always-on,
headless); an `@mcp.tool()` wrapper provides on-demand invocation from Claude Code.
[Source: g-api-mcp codebase analysis]

**Post-sync cleanup: PATCH `status=completed`, never DELETE.** Completing a task via API is
idempotent, reversible, and preserves task history. DELETE cascades to assigned tasks in
Docs/Chat Spaces and causes data loss on partial failures. The recommended two-layer safety
pattern: embed a `[synced:ISO8601]` marker in task notes after confirmed vault write, then PATCH
status=completed. The notes marker enables skip-on-retry without requiring state-file reads.
[Source: Google Tasks API tasks.patch, tasks.delete references]

**Deduplication uses HTML comment task IDs.** Each vault task line ends with
`<!-- gtask:{id} -->`. This is invisible to Obsidian readers, not parsed by the obsidian-tasks
plugin (queries are unaffected), and trivially greppable for upsert logic. [Source: Track 3
transformation mapping analysis, vault Task Management System.md]

## Solution Space

### Option A: Standalone Python script (separate repo)

**Summary:** Independent Python script, no dependency on g-api-mcp.
**Pros:** Isolated — changes don't affect the MCP server.
**Cons:** Duplicates auth, Google API client, test infrastructure. More maintenance surface.
**Recommendation:** Reject.

### Option B: New module in g-api-mcp package (recommended)

**Summary:** `sync.py` module + `g-api-mcp-sync` console script within the existing package.
Exposes both a CLI (for Task Scheduler) and optionally an MCP tool.
**Pros:** Reuses `GoogleCredentialManager` singleton, existing Google API client patterns,
test infrastructure (pytest + pytest-asyncio + mock pattern). Single place to maintain auth.
**Cons:** Changes to sync.py live alongside MCP server code — requires care not to break server.
**Recommendation:** Adopt.

### Option C: External daemon process

**Summary:** Long-running Python process with asyncio polling loop.
**Pros:** Lowest latency (can poll at <60s).
**Cons:** No crash recovery, no Windows service infrastructure, overkill for personal task volume.
**Recommendation:** Reject. Windows Task Scheduler provides the same effective interval with
built-in logging and restart behavior.

## Recommended Direction

**Option B** — new `sync.py` module in g-api-mcp + `g-api-mcp-sync` console script,
triggered by Windows Task Scheduler at 60-second intervals with an MCP tool wrapper for
on-demand invocation. State persisted at `%APPDATA%/g-api-mcp/sync-state.json`.

## Design Decisions Required

| Decision | Options | Recommendation | Open? |
|---|---|---|---|
| Poll interval | 30s / 60s / 5min | 60s (1,440 calls/day, safe) | No |
| Cleanup strategy | complete / delete / notes-marker | PATCH status=completed + notes marker for two-layer safety | No |
| Due date mapping | ⏳ always / 📅 on keywords / configurable | ⏳ always — Google Task due dates are intent, not hard deadlines | No |
| Default priority | ⏫ / 🔼 / 🔽 | 🔼 (should-do) for all incoming tasks | No |
| Task ID embedding | HTML comment / frontmatter / separate index | `<!-- gtask:{id} -->` at end of task line | No |
| Target note routing | Daily only / project match / configurable map | Project match first, fall back to daily note for due date | No |
| Config location | %APPDATA% JSON / project-local / env vars | `%APPDATA%/g-api-mcp/sync-config.json` | No |
| Conflict resolution | Vault wins / Google wins / merge | Vault wins for state/priority/date; Google Tasks wins for title/notes text | No |
| Recurring task cleanup | Complete / skip / notes-only | Notes-only marker until recurring behavior confirmed empirically | Yes |
| Multi-tasklist support | All lists / configured list IDs / name patterns | Configured list IDs + name→project mapping in sync-config.json | No |

## Known Limitations & Gaps

- **Recurring tasks cleanup**: PATCH `status=completed` on a recurring task may spawn a new
  instance (same behavior as DELETE). Confirmed for DELETE, unconfirmed for PATCH. Until
  empirically tested, use notes-only marker for tasks where `recurrence` field is non-null.
  (Note: the v1 Tasks API does not expose a recurrence field directly — detect via task re-
  appearance after completion.)
- **ETag fast path**: The collection-level `etag` may support `If-None-Match` for a cheap
  "nothing changed" check before full list fetch. Not confirmed working for Tasks API. Treat
  as a Phase 2+ optimization.
- **Offline Gemini tasks**: Tasks created offline by Gemini may have a creation timestamp
  rather than a modified timestamp as their `updated` value. If `updated` precedes the last
  sync timestamp, the task will be missed. Mitigation: on first run (or weekly), do a full
  sync without `updatedMin`.
- **No inbox routing layer**: The vault has no inbox. The sync tool must determine the target
  note at insertion time. New tasklist names with no project match go to the daily note with
  a normalized list-name hashtag.
- **Subtask parent lookup**: Subtasks that arrive before their parent task is in the vault are
  inserted at top level with a `[Subtask]` prefix and re-parented on the next sync pass.

## Implementation Phases

See SPEC-phase-1.md through SPEC-phase-4.md.

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Polling infrastructure: `sync.py` core loop, config schema, state file, `updatedMin` param | complete |
| 2 | Transformation + routing: `_to_vault_line()`, routing algorithm, project-match logic | complete |
| 3 | Vault write + cleanup: dedup upsert, notes marker, PATCH completed, Task Scheduler setup | complete |
| 4 | Test harness: 17-test corpus across 5 layers, e2e smoke test, baseline review | complete |

> **Next action:** Register Task Scheduler — `powershell -ExecutionPolicy Bypass -File scripts\register-task-scheduler.ps1` (add Scripts dir to PATH first); then merge PR #2

## Sources

1. [Google Tasks API: tasks.list](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks/list) — updatedMin, showDeleted, rate limits
2. [Google Tasks API: tasks.patch](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks/patch) — idempotent completion
3. [Google Tasks API: tasks.delete](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks/delete) — cascade behavior, avoid for cleanup
4. [Google Tasks API: Task resource schema](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks#Task) — full field list
5. [Google Workspace Events API](https://developers.google.com/workspace/events) — Tasks not covered, no push notifications
6. [Google Tasks API: tasks.clear](https://developers.google.com/workspace/tasks/reference/rest/v1/tasklists/clear) — hidden/completed task behavior
7. C:\Obsidian\Hivemind\30 Library\Task Management System.md — authoritative vault task syntax
8. C:\Obsidian\Hivemind\00 Task Dashboard\Tasks.md — dashboard queries synced tasks must satisfy
9. C:\EpicSource\Projects\g-api-mcp\src\g_api_mcp\tasks.py — existing tool patterns
10. C:\EpicSource\Projects\g-api-mcp\src\g_api_mcp\auth.py — credential manager reuse
11. C:\EpicSource\Projects\g-api-mcp\tests\test_tasks.py — test infrastructure patterns
