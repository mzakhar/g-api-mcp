"""
Tests for Calendar tool handlers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from g_api_mcp import calendar as cal_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_event(
    event_id: str = "evt1",
    summary: str = "Team Standup",
    start: str = "2026-04-06T09:00:00-05:00",
    end: str = "2026-04-06T09:15:00-05:00",
    status: str = "confirmed",
    attendees: list[dict] | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    event: dict = {
        "id": event_id,
        "iCalUID": f"{event_id}@google.com",
        "summary": summary,
        "status": status,
        "start": {"dateTime": start, "timeZone": "America/Chicago"},
        "end": {"dateTime": end, "timeZone": "America/Chicago"},
        "organizer": {"email": "organizer@example.com"},
        "attendees": attendees or [],
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
        "created": "2026-01-01T00:00:00.000Z",
        "updated": "2026-01-02T00:00:00.000Z",
    }
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    return event


# ---------------------------------------------------------------------------
# calendar_list_calendars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_calendars_returns_all():
    service = MagicMock()
    service.calendarList().list().execute.return_value = {
        "items": [
            {"id": "primary", "summary": "My Calendar", "primary": True,
             "accessRole": "owner", "timeZone": "America/Chicago"},
            {"id": "work@example.com", "summary": "Work", "primary": False,
             "accessRole": "writer", "timeZone": "America/Chicago"},
        ]
    }

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_list_calendars()

    env = json.loads(result)
    assert env["success"] is True
    assert len(env["data"]) == 2
    assert env["data"][0]["primary"] is True
    assert env["pagination"]["has_more"] is False


# ---------------------------------------------------------------------------
# calendar_list_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_events_thin_summaries():
    events = [make_event("e1", "Standup"), make_event("e2", "Lunch")]
    service = MagicMock()
    service.events().list().execute.return_value = {"items": events}

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_list_events()

    env = json.loads(result)
    assert env["success"] is True
    assert len(env["data"]) == 2
    # Thin — no description, no full attendee list
    for ev in env["data"]:
        assert "description" not in ev
        assert "id" in ev
        assert "summary" in ev
        assert "start" in ev
        assert "end" in ev


@pytest.mark.asyncio
async def test_list_events_pagination():
    service = MagicMock()
    service.events().list().execute.return_value = {
        "items": [make_event()],
        "nextPageToken": "page2",
    }

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_list_events()

    env = json.loads(result)
    assert env["pagination"]["has_more"] is True
    assert env["pagination"]["next_cursor"] == "page2"


@pytest.mark.asyncio
async def test_list_events_max_results_capped():
    service = MagicMock()
    service.events().list().execute.return_value = {"items": []}

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await cal_module.calendar_list_events(max_results=9999)

    call_kwargs = service.events().list.call_args.kwargs
    assert call_kwargs["maxResults"] <= 250


# ---------------------------------------------------------------------------
# calendar_get_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_event_full_fields():
    event = make_event(
        "e1",
        description="Daily sync",
        location="Zoom",
        attendees=[
            {"email": "alice@example.com", "responseStatus": "accepted"},
            {"email": "bob@example.com", "responseStatus": "needsAction"},
        ],
    )
    service = MagicMock()
    service.events().get().execute.return_value = event

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_get_event(event_id="e1")

    env = json.loads(result)
    assert env["success"] is True
    assert env["pagination"] is None
    assert env["data"]["description"] == "Daily sync"
    assert env["data"]["location"] == "Zoom"
    assert len(env["data"]["attendees"]) == 2


# ---------------------------------------------------------------------------
# calendar_create_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_event_timed():
    created = make_event("new1", summary="New Meeting")
    service = MagicMock()
    service.events().insert().execute.return_value = created

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_create_event(
            summary="New Meeting",
            start="2026-04-10T14:00:00-05:00",
            end="2026-04-10T15:00:00-05:00",
        )

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["event_id"] == "new1"
    assert "html_link" in env["data"]

    # Check that dateTime was used (not date)
    body_arg = service.events().insert.call_args.kwargs["body"]
    assert "dateTime" in body_arg["start"]


@pytest.mark.asyncio
async def test_create_event_all_day():
    created = make_event("new2")
    created["start"] = {"date": "2026-04-10"}
    created["end"] = {"date": "2026-04-11"}
    service = MagicMock()
    service.events().insert().execute.return_value = created

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        await cal_module.calendar_create_event(
            summary="All Day",
            start="2026-04-10",
            end="2026-04-11",
        )

    body_arg = service.events().insert.call_args.kwargs["body"]
    assert "date" in body_arg["start"]
    assert "dateTime" not in body_arg["start"]


# ---------------------------------------------------------------------------
# calendar_update_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_event_partial():
    service = MagicMock()
    service.events().patch().execute.return_value = {
        "id": "e1",
        "updated": "2026-04-05T12:00:00.000Z",
    }

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_update_event(
            event_id="e1",
            summary="Updated Title",
        )

    env = json.loads(result)
    assert env["success"] is True
    patch_body = service.events().patch.call_args.kwargs["body"]
    assert patch_body["summary"] == "Updated Title"
    assert "start" not in patch_body  # not supplied → not sent


@pytest.mark.asyncio
async def test_update_event_no_fields_raises():
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError):
        await cal_module.calendar_update_event(event_id="e1")


# ---------------------------------------------------------------------------
# calendar_delete_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_event():
    service = MagicMock()
    service.events().delete().execute.return_value = None  # 204 No Content

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_delete_event(event_id="e1")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["deleted"] is True


# ---------------------------------------------------------------------------
# calendar_quick_add
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quick_add():
    service = MagicMock()
    service.events().quickAdd().execute.return_value = {
        "id": "qa1",
        "summary": "Lunch with Alex",
        "start": {"dateTime": "2026-04-06T12:00:00-05:00"},
        "end": {"dateTime": "2026-04-06T13:00:00-05:00"},
        "htmlLink": "https://calendar.google.com/event?eid=qa1",
    }

    with patch.object(cal_module, "_calendar_service", return_value=service), \
         patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn()):
        result = await cal_module.calendar_quick_add(text="Lunch with Alex tomorrow at noon")

    env = json.loads(result)
    assert env["success"] is True
    assert env["data"]["summary"] == "Lunch with Alex"
    assert env["data"]["start"] is not None


# ---------------------------------------------------------------------------
# _to_thin_event / _to_full_event helpers
# ---------------------------------------------------------------------------


def test_to_thin_event_strips_description():
    event = make_event("e1", description="Should not appear")
    thin = cal_module._to_thin_event(event)
    assert "description" not in thin
    assert thin["summary"] == "Team Standup"
    assert thin["attendee_count"] == 0


def test_to_thin_event_all_day():
    event = {
        "id": "e1",
        "summary": "Conference",
        "status": "confirmed",
        "start": {"date": "2026-04-10"},
        "end": {"date": "2026-04-12"},
        "organizer": {},
        "attendees": [],
    }
    thin = cal_module._to_thin_event(event)
    assert thin["start"] == "2026-04-10"


def test_to_full_event_includes_attendees():
    attendees = [
        {"email": "a@example.com", "responseStatus": "accepted"},
        {"email": "b@example.com", "responseStatus": "declined"},
    ]
    event = make_event("e1", attendees=attendees, description="Notes here")
    full = cal_module._to_full_event(event)
    assert full["description"] == "Notes here"
    assert len(full["attendees"]) == 2


def test_extract_conference_link():
    event = make_event("e1")
    event["conferenceData"] = {
        "entryPoints": [
            {"entryPointType": "phone", "uri": "tel:+1234567890"},
            {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
        ]
    }
    link = cal_module._extract_conference_link(event)
    assert link == "https://meet.google.com/abc-defg-hij"


def test_extract_conference_link_none():
    assert cal_module._extract_conference_link(make_event("e1")) is None
