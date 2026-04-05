"""
Google Contacts (People API) MCP tools.

Tools
-----
contacts_list    — thin list of contacts (name, email, phone)
contacts_get     — full details for one contact by resourceName
contacts_search  — search contacts by name or email keyword
contacts_create  — create a new contact
contacts_update  — update fields on an existing contact
contacts_delete  — permanently delete a contact
"""

from __future__ import annotations

import asyncio
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

# Fields to request for thin list results
_THIN_FIELDS = "names,emailAddresses,phoneNumbers,metadata"
# Fields to request for full contact details
_FULL_FIELDS = (
    "names,emailAddresses,phoneNumbers,addresses,organizations,"
    "birthdays,biographies,urls,metadata"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _people_service():
    creds = await asyncio.to_thread(cred_manager.get_valid_credentials)
    return build("people", "v1", credentials=creds, cache_discovery=False)


def _http_error_message(e: HttpError) -> str:
    try:
        detail = json.loads(e.content.decode()).get("error", {}).get("message", str(e))
    except Exception:
        detail = str(e)
    return f"Google API error {e.resp.status}: {detail}"


def _primary(items: list[dict], field: str) -> str | None:
    """Return the value of the first primary (or first) entry from a repeated field."""
    if not items:
        return None
    primary = next((i for i in items if i.get("metadata", {}).get("primary")), items[0])
    return primary.get(field)


def _to_thin_contact(person: dict) -> dict:
    names = person.get("names", [])
    emails = person.get("emailAddresses", [])
    phones = person.get("phoneNumbers", [])
    return {
        "resourceName": person.get("resourceName"),
        "name": _primary(names, "displayName"),
        "email": _primary(emails, "value"),
        "phone": _primary(phones, "value"),
        "etag": person.get("etag"),
    }


def _to_full_contact(person: dict) -> dict:
    return {
        "resourceName": person.get("resourceName"),
        "etag": person.get("etag"),
        "names": [
            {"displayName": n.get("displayName"), "givenName": n.get("givenName"),
             "familyName": n.get("familyName")}
            for n in person.get("names", [])
        ],
        "emailAddresses": [
            {"value": e.get("value"), "type": e.get("type"),
             "primary": e.get("metadata", {}).get("primary", False)}
            for e in person.get("emailAddresses", [])
        ],
        "phoneNumbers": [
            {"value": p.get("value"), "type": p.get("type"),
             "primary": p.get("metadata", {}).get("primary", False)}
            for p in person.get("phoneNumbers", [])
        ],
        "addresses": [
            {"formattedValue": a.get("formattedValue"), "type": a.get("type")}
            for a in person.get("addresses", [])
        ],
        "organizations": [
            {"name": o.get("name"), "title": o.get("title")}
            for o in person.get("organizations", [])
        ],
        "birthdays": [
            b.get("text") or (
                "{year}-{month:02d}-{day:02d}".format(**b["date"])
                if "date" in b else None
            )
            for b in person.get("birthdays", [])
        ],
        "biographies": [b.get("value") for b in person.get("biographies", [])],
        "urls": [{"value": u.get("value"), "type": u.get("type")} for u in person.get("urls", [])],
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def contacts_list(
    max_results: int = 20,
    page_cursor: str | None = None,
) -> str:
    """List Google Contacts. Returns thin summaries (name, email, phone).
    Use contacts_get to fetch full details for a specific contact.

    Args:
        max_results: Number of contacts per page (1–1000). Default 20.
        page_cursor: Pass pagination.next_cursor from a prior call to get the next page.
    """
    max_results = max(1, min(max_results, 1000))

    try:
        service = await _people_service()
        kwargs: dict[str, Any] = {
            "resourceName": "people/me",
            "pageSize": max_results,
            "personFields": _THIN_FIELDS,
            "sortOrder": "LAST_NAME_ASCENDING",
        }
        if page_cursor:
            kwargs["pageToken"] = page_cursor

        result = await asyncio.to_thread(
            lambda: service.people().connections().list(**kwargs).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    contacts = [_to_thin_contact(p) for p in result.get("connections", [])]
    env = build_envelope(
        data=contacts,
        has_more="nextPageToken" in result,
        next_cursor=result.get("nextPageToken"),
    )
    return json.dumps(env)


@mcp.tool()
async def contacts_get(resource_name: str) -> str:
    """Fetch full details for a single contact.

    Args:
        resource_name: Contact resourceName from contacts_list or contacts_search
                       (e.g. "people/c1234567890").
    """
    try:
        service = await _people_service()
        person = await asyncio.to_thread(
            lambda: service.people().get(
                resourceName=resource_name,
                personFields=_FULL_FIELDS,
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data=_to_full_contact(person), is_list=False)
    return json.dumps(env)


@mcp.tool()
async def contacts_search(
    query: str,
    max_results: int = 10,
) -> str:
    """Search Google Contacts by name, email, phone, or other text.

    Args:
        query: Search text (name fragment, email address, etc.).
        max_results: Maximum results to return (1–30). Default 10.
    """
    max_results = max(1, min(max_results, 30))

    try:
        service = await _people_service()
        result = await asyncio.to_thread(
            lambda: service.people().searchContacts(
                query=query,
                pageSize=max_results,
                readMask=_THIN_FIELDS,
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    contacts = [
        _to_thin_contact(r["person"])
        for r in result.get("results", [])
        if "person" in r
    ]
    env = build_envelope(data=contacts, result_count=len(contacts), has_more=False)
    return json.dumps(env)


@mcp.tool()
async def contacts_create(
    given_name: str,
    family_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    notes: str | None = None,
) -> str:
    """Create a new Google Contact.

    Args:
        given_name: First name (required).
        family_name: Last name.
        email: Primary email address.
        phone: Primary phone number.
        company: Organization/company name.
        job_title: Job title within the organization.
        notes: Free-text biography or notes.
    """
    body: dict[str, Any] = {
        "names": [{"givenName": given_name, "familyName": family_name or ""}],
    }
    if email:
        body["emailAddresses"] = [{"value": email, "type": "work"}]
    if phone:
        body["phoneNumbers"] = [{"value": phone, "type": "work"}]
    if company or job_title:
        body["organizations"] = [{"name": company or "", "title": job_title or ""}]
    if notes:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]

    try:
        service = await _people_service()
        person = await asyncio.to_thread(
            lambda: service.people().createContact(body=body).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data=_to_thin_contact(person), is_list=False)
    return json.dumps(env)


@mcp.tool()
async def contacts_update(
    resource_name: str,
    given_name: str | None = None,
    family_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    notes: str | None = None,
) -> str:
    """Update fields on an existing contact (replaces the supplied field groups entirely).
    Only the field groups you provide are changed; omitted fields are left untouched.

    Args:
        resource_name: Contact resourceName (e.g. "people/c1234567890").
        given_name: New first name.
        family_name: New last name.
        email: Replaces ALL email addresses with this single address.
        phone: Replaces ALL phone numbers with this single number.
        company: Organization name.
        job_title: Job title within the organization.
        notes: Free-text biography/notes (replaces existing notes).
    """
    # Fetch current contact first so we have the etag and can merge
    try:
        service = await _people_service()
    except RuntimeError as e:
        return error_envelope(str(e))

    try:
        current = await asyncio.to_thread(
            lambda: service.people().get(
                resourceName=resource_name,
                personFields=_FULL_FIELDS,
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    update_fields: list[str] = []
    body: dict[str, Any] = {"etag": current["etag"]}

    if given_name is not None or family_name is not None:
        existing_name = (current.get("names") or [{}])[0]
        body["names"] = [{
            "givenName": given_name if given_name is not None else existing_name.get("givenName", ""),
            "familyName": family_name if family_name is not None else existing_name.get("familyName", ""),
        }]
        update_fields.append("names")

    if email is not None:
        body["emailAddresses"] = [{"value": email, "type": "work"}]
        update_fields.append("emailAddresses")

    if phone is not None:
        body["phoneNumbers"] = [{"value": phone, "type": "work"}]
        update_fields.append("phoneNumbers")

    if company is not None or job_title is not None:
        existing_org = (current.get("organizations") or [{}])[0]
        body["organizations"] = [{
            "name": company if company is not None else existing_org.get("name", ""),
            "title": job_title if job_title is not None else existing_org.get("title", ""),
        }]
        update_fields.append("organizations")

    if notes is not None:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
        update_fields.append("biographies")

    if not update_fields:
        raise ToolError("No fields to update — provide at least one field to change.")

    try:
        person = await asyncio.to_thread(
            lambda: service.people().updateContact(
                resourceName=resource_name,
                updatePersonFields=",".join(update_fields),
                body=body,
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data=_to_thin_contact(person), is_list=False)
    return json.dumps(env)


@mcp.tool()
async def contacts_delete(resource_name: str) -> str:
    """Permanently delete a Google Contact. This cannot be undone.

    Args:
        resource_name: Contact resourceName (e.g. "people/c1234567890").
    """
    try:
        service = await _people_service()
        await asyncio.to_thread(
            lambda: service.people().deleteContact(
                resourceName=resource_name
            ).execute()
        )
    except HttpError as e:
        return error_envelope(_http_error_message(e))
    except RuntimeError as e:
        return error_envelope(str(e))

    env = build_envelope(data={"deleted": True}, is_list=False)
    return json.dumps(env)
