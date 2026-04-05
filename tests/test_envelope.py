"""Unit tests for envelope.py — no mocks needed."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from g_api_mcp.envelope import build_envelope, error_envelope, estimate_tokens, WARN_MODERATE, WARN_LARGE


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty():
    assert estimate_tokens(None) == 1  # "null" = 4 chars // 4


def test_estimate_tokens_string():
    # 400 x's + 2 quote chars = 402 chars → 402 // 4 = 100
    obj = "x" * 400
    assert estimate_tokens(obj) == 100


def test_estimate_tokens_list():
    items = [{"id": str(i), "subject": "hello world"} for i in range(20)]
    tokens = estimate_tokens(items)
    assert tokens > 0
    # Sanity: 20 small objects should be well under 500 tokens
    assert tokens < 500


def test_estimate_tokens_large():
    big = {"text": "a" * 40_000}
    assert estimate_tokens(big) > WARN_LARGE


# ---------------------------------------------------------------------------
# build_envelope — success / list
# ---------------------------------------------------------------------------


def test_list_envelope_basic():
    data = [{"id": "1"}, {"id": "2"}]
    env = build_envelope(data=data)

    assert env["success"] is True
    assert env["data"] == data
    assert env["error"] is None
    assert env["pagination"]["result_count"] == 2
    assert env["pagination"]["has_more"] is False
    assert env["pagination"]["next_cursor"] is None
    assert env["pagination"]["total_estimate"] is None
    assert env["context_hint"]["estimated_tokens"] > 0


def test_list_envelope_with_pagination():
    data = [{"id": str(i)} for i in range(20)]
    env = build_envelope(
        data=data,
        has_more=True,
        next_cursor="tok_abc",
        total_estimate=847,
    )

    assert env["pagination"]["has_more"] is True
    assert env["pagination"]["next_cursor"] == "tok_abc"
    assert env["pagination"]["result_count"] == 20
    assert env["pagination"]["total_estimate"] == 847


def test_list_envelope_explicit_result_count():
    env = build_envelope(data=[], result_count=0, has_more=False)
    assert env["pagination"]["result_count"] == 0


# ---------------------------------------------------------------------------
# build_envelope — success / singleton
# ---------------------------------------------------------------------------


def test_singleton_envelope_no_pagination():
    env = build_envelope(data={"id": "abc", "body": "hello"}, is_list=False)

    assert env["success"] is True
    assert env["pagination"] is None
    assert env["data"]["id"] == "abc"


# ---------------------------------------------------------------------------
# build_envelope — error
# ---------------------------------------------------------------------------


def test_error_envelope_sets_success_false():
    env = build_envelope(error="something broke", is_list=False)

    assert env["success"] is False
    assert env["data"] is None
    assert env["error"] == "something broke"
    assert env["pagination"] is None


def test_error_envelope_helper_returns_json_string():
    result = error_envelope("boom")
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["error"] == "boom"


# ---------------------------------------------------------------------------
# context_hint warnings
# ---------------------------------------------------------------------------


def test_no_warning_for_small_payload():
    small = [{"id": str(i)} for i in range(5)]
    env = build_envelope(data=small)
    assert env["context_hint"]["warning"] is None


def test_moderate_warning_fires():
    # Craft a payload that is between WARN_MODERATE and WARN_LARGE
    # Target ~6,000 tokens → ~24,000 chars of JSON
    payload = {"text": "a" * (WARN_MODERATE * 4 + 100)}
    env = build_envelope(data=payload, is_list=False)
    tokens = env["context_hint"]["estimated_tokens"]
    assert WARN_MODERATE < tokens <= WARN_LARGE
    assert env["context_hint"]["warning"] is not None
    assert "moderate" in env["context_hint"]["warning"]


def test_large_warning_fires():
    payload = {"text": "a" * (WARN_LARGE * 4 + 100)}
    env = build_envelope(data=payload, is_list=False)
    tokens = env["context_hint"]["estimated_tokens"]
    assert tokens > WARN_LARGE
    assert env["context_hint"]["warning"] is not None
    assert "large" in env["context_hint"]["warning"]


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_envelope_is_json_serialisable():
    data = [{"id": "1", "subject": "Test", "from": "a@b.com"}]
    env = build_envelope(data=data, has_more=True, next_cursor="tok")
    dumped = json.dumps(env)
    parsed = json.loads(dumped)
    assert parsed["pagination"]["next_cursor"] == "tok"
