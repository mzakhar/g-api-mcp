"""
Response envelope utilities.

Every MCP tool returns a JSON-serialised McpEnvelope so the LLM can read
pagination state and an estimated token count before deciding whether to
fetch more data.

Schema
------
{
  "success": bool,
  "data": <T> | null,
  "pagination": {                   // null for singleton (get_*) responses
    "has_more": bool,
    "next_cursor": str | null,      // opaque pageToken from upstream API
    "result_count": int,            // items in THIS page
    "total_estimate": int | null    // upstream resultSizeEstimate when available
  } | null,
  "context_hint": {
    "estimated_tokens": int,        // len(json.dumps(data)) // 4
    "warning": str | null           // non-null when payload is large
  },
  "error": str | null               // null on success
}

Token warning thresholds
------------------------
  < 2 000  → no warning
  2 000–8 000 → soft: "moderate payload…"
  > 8 000  → hard: "large payload…"
"""

from __future__ import annotations

import json
from typing import Any

WARN_MODERATE = 2_000
WARN_LARGE = 8_000


def estimate_tokens(obj: Any) -> int:
    """
    Fast token estimate: ~4 chars per token for English/JSON text.
    Accurate to ±25 % — sufficient for pre-flight planning decisions.
    """
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def build_envelope(
    *,
    data: Any = None,
    has_more: bool = False,
    next_cursor: str | None = None,
    result_count: int | None = None,
    total_estimate: int | None = None,
    is_list: bool = True,
    error: str | None = None,
) -> dict:
    """
    Build a standardised response envelope.

    Parameters
    ----------
    data:
        The payload — a list for collection responses, a dict for singletons.
    has_more:
        True when a next page exists. Only relevant when is_list=True.
    next_cursor:
        The nextPageToken to pass as page_cursor in the next call.
    result_count:
        Number of items in data. Auto-computed from len(data) if omitted.
    total_estimate:
        resultSizeEstimate from the upstream API, when available.
    is_list:
        False for singleton (get_*) responses — omits the pagination block.
    error:
        Human-readable error string. When set, data is replaced with null.
    """
    success = error is None
    tokens = estimate_tokens(data)

    warning: str | None = None
    if tokens > WARN_LARGE:
        warning = (
            f"large payload (~{tokens:,} tokens) — consider fetching "
            "specific items by ID rather than consuming the full response"
        )
    elif tokens > WARN_MODERATE:
        warning = (
            f"moderate payload (~{tokens:,} tokens) — use get_* tools "
            "for specific IDs if you need only a subset"
        )

    pagination: dict | None = None
    if is_list:
        count = (
            result_count
            if result_count is not None
            else (len(data) if isinstance(data, list) else 0)
        )
        pagination = {
            "has_more": has_more,
            "next_cursor": next_cursor,
            "result_count": count,
            "total_estimate": total_estimate,
        }

    return {
        "success": success,
        "data": data if success else None,
        "pagination": pagination,
        "context_hint": {"estimated_tokens": tokens, "warning": warning},
        "error": error,
    }


def error_envelope(message: str) -> str:
    """Convenience: return a JSON-serialised error envelope."""
    env = build_envelope(error=message, is_list=False)
    return json.dumps(env)
