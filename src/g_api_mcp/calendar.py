"""
Google Calendar MCP tools.

Tools
-----
calendar_list_calendars — all calendars the user has access to
calendar_list_events    — thin event summaries for a time range
calendar_get_event      — full event details for one event ID
calendar_create_event   — create a new event
calendar_update_event   — partial update an existing event
calendar_delete_event   — delete an event
calendar_quick_add      — natural-language event creation
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from mcp.server.fastmcp.exceptions import ToolError

from g_api_mcp.auth import cred_manager
from g_api_mcp.envelope import build_envelope, error_envelope
from g_api_mcp.server import mcp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _calendar_service():
    creds = await asyncio.to_thread(cred_manager.get_valid_credentials)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _http_error_message(e: HttpError) -> str:
    try:
        detail = json.loads(e.content.decode()).get("error", {}).get("message", str(e))
    except Exception:
        detail = str(e)
    return f"Google API error {e.resp.status}: {detail}"


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _plus_days(n: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=n)
    return dt.isoformat()


def _to_thin_event(event: dict) -> dict:
    """Collapse a full Calendar event to a lightweight summary."""
    start = event.get("start", {})
    end = event.get("end", {})
    attendees = event.get("attendees", [])
    organizer = event.get("organizer", {})
    return {
        "id": event["id"],
        "summary": event.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "status": event.get("status", "confirmed"),
        "organizer_email": organizer.get("email", ""),
        "attendee_count": len(attendees),
        "location": event.get("location"),
        "recurring_event_id": event.get("recurringEventId"),
        "transparency": event.get("transparency", "opaque"),
    }


def _to_full_event(event: dict) -> dict:
    """Return the relevant fields from a full Calendar event."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event["id"],
        "i_cal_uid": event.get("iCalUID"),
        "summary": event.get("summary", "(no title)"),
        "description": event.get("description"),
        "location": event.get("location"),
        "start": start.get("dateTime") or start.get("date"),
        "start_timezone": start.get("timeZone"),
        "end": end.get("dateTime") or end.get("date"),
        "end_timezone": end.get("timeZone"),
        "status": event.get("status", "confirmed"),
        "transparency": event.get("transparency", "opaque"),
        "visibility": event.get("visibility", "default"),
        "organizer": event.get("organizer", {}),
        "attendees": event.get("attendees", []),
        "recurrence": event.get("recurrence", []),
        "recurring_event_id": event.get("recurringEventId"),
        "original_start_time": event.get("originalStartTime", {}).get("dateTime")
            or event.get("originalStartTime", {}).get("date"),
        "conference_link": _extract_conference_link(event),
        "html_link": event.get("htmlLink"),
        "created": event.get("created"),
        "updated": event.get("updated"),
    }


def _extract_conference_link(event: dict) -> str | None:
    """Pull the Meet/Zoom/Teams link out of conferenceData if present."""
    conf = event.get("conferenceData", {})
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri")
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def calendar_list_calendars() -> str:
    """List all Google Calendars the user has access to.
    Returns calendar IDs needed for other calendar_* tools.
    """
    try:
        service = await _calendar_service()
        result = await asyncio.to_thread(
            lambda: service.calendarList().list(maxResults=250).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    calendars = [
        {
            "id": cal["id"],
            "summary": cal.get("summary", ""),
            "description": cal.get("description"),
            "time_zone": cal.get("timeZone"),
            "primary": cal.get("primary", False),
            "access_role": cal.get("accessRole", ""),
            "selected": cal.get("selected", True),
        }
        for cal in result.get("items", [])
    ]
    env = build_envelope(data=calendars, result_count=len(calendars), has_more=False)
    return json.dumps(env)


@mcp.tool()
async def calendar_list_events(
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 25,
    page_cursor: str | None = None,
) -> str:
    """List events from a Google Calendar. Returns thin summaries (no description or attendee details).
    Use calendar_get_event for full details on specific events.

    Args:
        calendar_id: Calendar ID. Use "primary" for the main calendar, or an ID
                     from calendar_list_calendars.
        time_min: RFC 3339 start of range (inclusive). Defaults to now.
        time_max: RFC 3339 end of range (exclusive). Defaults to 7 days from now.
        query: Free-text search across event title, description, location, and attendees.
        max_results: Events per page (1–250). Default 25.
        page_cursor: Pass pagination.next_cursor from a prior call to get the next page.
    """
    max_results = max(1, min(max_results, 250))
    t_min = time_min or _now_utc()
    t_max = time_max or _plus_days(7)

    try:
        service = await _calendar_service()
        kwargs: dict[str, Any] = {
            "calendarId": calendar_id,
            "timeMin": t_min,
            "timeMax": t_max,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if query:
            kwargs["q"] = query
        if page_cursor:
            kwargs["pageToken"] = page_cursor

        result = await asyncio.to_thread(
            lambda: service.events().list(**kwargs).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    thin_events = [_to_thin_event(ev) for ev in result.get("items", [])]
    env = build_envelope(
        data=thin_events,
        has_more="nextPageToken" in result,
        next_cursor=result.get("nextPageToken"),
    )
    return json.dumps(env)


@mcp.tool()
async def calendar_get_event(
    event_id: str,
    calendar_id: str = "primary",
) -> str:
    """Fetch full details for a single Calendar event.
    Includes description, all attendees with response status, conference link,
    recurrence rules, and location.

    Args:
        event_id: Event ID from a calendar_list_events response.
        calendar_id: Calendar that owns this event. Default "primary".
    """
    try:
        service = await _calendar_service()
        event = await asyncio.to_thread(
            lambda: service.events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data=_to_full_event(event), is_list=False)
    return json.dumps(env)


@mcp.tool()
async def calendar_create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str | None = None,
    attendees: list[str] | None = None,
    location: str | None = None,
    send_notifications: bool = True,
) -> str:
    """Create a new Google Calendar event.

    Args:
        summary: Event title.
        start: Start datetime in RFC 3339 format (e.g. "2026-04-10T14:00:00-05:00").
               For all-day events use date format: "2026-04-10".
        end: End datetime in RFC 3339 format.
        calendar_id: Target calendar. Default "primary".
        description: Event notes/body text.
        attendees: Email addresses to invite.
        location: Location string or address.
        send_notifications: Email invites to attendees. Default true.
    """
    event_body: dict[str, Any] = {"summary": summary}

    # Detect all-day (date-only) vs timed events
    if "T" in start:
        event_body["start"] = {"dateTime": start}
        event_body["end"] = {"dateTime": end}
    else:
        event_body["start"] = {"date": start}
        event_body["end"] = {"date": end}

    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location
    if attendees:
        event_body["attendees"] = [{"email": e} for e in attendees]

    try:
        service = await _calendar_service()
        result = await asyncio.to_thread(
            lambda: service.events().insert(
                calendarId=calendar_id,
                body=event_body,
                sendUpdates="all" if send_notifications else "none",
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    start_val = result.get("start", {})
    env = build_envelope(
        data={
            "event_id": result["id"],
            "summary": result.get("summary"),
            "start": start_val.get("dateTime") or start_val.get("date"),
            "html_link": result.get("htmlLink"),
        },
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def calendar_update_event(
    event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    send_notifications: bool = True,
) -> str:
    """Partially update an existing Calendar event (PATCH semantics — only supplied
    fields are changed; omitted fields keep their current values).

    Args:
        event_id: Event ID to update.
        calendar_id: Calendar that owns the event. Default "primary".
        summary: New title (omit to keep current).
        start: New start datetime in RFC 3339 format (omit to keep current).
        end: New end datetime in RFC 3339 format (omit to keep current).
        description: New description (omit to keep current).
        location: New location (omit to keep current).
        send_notifications: Notify attendees of the change. Default true.
    """
    patch: dict[str, Any] = {}
    if summary is not None:
        patch["summary"] = summary
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if start is not None:
        patch["start"] = {"dateTime": start} if "T" in start else {"date": start}
    if end is not None:
        patch["end"] = {"dateTime": end} if "T" in end else {"date": end}

    if not patch:
        raise ToolError("No fields to update — provide at least one of: summary, start, end, description, location.")

    try:
        service = await _calendar_service()
        result = await asyncio.to_thread(
            lambda: service.events().patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=patch,
                sendUpdates="all" if send_notifications else "none",
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(
        data={"event_id": result["id"], "updated": result.get("updated")},
        is_list=False,
    )
    return json.dumps(env)


@mcp.tool()
async def calendar_delete_event(
    event_id: str,
    calendar_id: str = "primary",
    send_notifications: bool = True,
) -> str:
    """Delete a Calendar event.

    Args:
        event_id: Event ID to delete.
        calendar_id: Calendar that owns the event. Default "primary".
        send_notifications: Send cancellation notices to attendees. Default true.
    """
    try:
        service = await _calendar_service()
        await asyncio.to_thread(
            lambda: service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendUpdates="all" if send_notifications else "none",
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data={"deleted": True}, is_list=False)
    return json.dumps(env)


@mcp.tool()
async def calendar_quick_add(
    text: str,
    calendar_id: str = "primary",
) -> str:
    """Create a Calendar event from a natural-language description.
    Google parses the text to extract time, title, and location.
    Always confirm the parsed start time with the user before proceeding.

    Args:
        text: Natural-language event description, e.g. "Lunch with Alex tomorrow at noon"
              or "Team standup every weekday at 9am".
        calendar_id: Target calendar. Default "primary".
    """
    try:
        service = await _calendar_service()
        result = await asyncio.to_thread(
            lambda: service.events().quickAdd(
                calendarId=calendar_id,
                text=text,
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    start_val = result.get("start", {})
    end_val = result.get("end", {})
    env = build_envelope(
        data={
            "event_id": result["id"],
            "summary": result.get("summary"),
            "start": start_val.get("dateTime") or start_val.get("date"),
            "end": end_val.get("dateTime") or end_val.get("date"),
            "html_link": result.get("htmlLink"),
        },
        is_list=False,
    )
    return json.dumps(env)
