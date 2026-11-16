"""CRM adapters. Called only from the outbox worker, never from a request.

* LocalCRM: our own `leads`/`contacts` tables are the CRM (default; free, no account).
* HubSpotCRM: HubSpot Free CRM via a private-app token. Upserts the contact by email,
  sets lead status, and attaches a note with the qualification summary.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from assistant.outbox.errors import PermanentError

HUBSPOT_API = "https://api.hubapi.com"
NOTE_TO_CONTACT = 202  # HubSpot-defined association type id
_STATUS = {"new": "NEW", "qualified": "OPEN", "enterprise": "OPEN", "unqualified": "UNQUALIFIED"}


@dataclass
class LeadSync:
    email: str
    name: str | None
    phone: str | None
    company: str | None
    status: str
    summary: str


class CRMAdapter(Protocol):
    name: str

    async def upsert_lead(self, lead: LeadSync, key: str) -> str: ...


class LocalCRM:
    name = "local"

    async def upsert_lead(self, lead: LeadSync, key: str) -> str:
        return "local"


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    msg = f"hubspot {resp.status_code}: {resp.text[:300]}"
    if resp.status_code in (400, 401, 403, 404):
        raise PermanentError(msg)  # bad token / payload: retrying won't help
    resp.raise_for_status()  # 429 / 5xx -> retried by the worker


class HubSpotCRM:
    name = "hubspot"

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=HUBSPOT_API, timeout=15, headers={"Authorization": f"Bearer {token}"}
        )

    async def _find_contact(self, email: str) -> str | None:
        r = await self._client.post(
            "/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
                ],
                "properties": ["email"],
                "limit": 1,
            },
        )
        _raise_for(r)
        results = r.json().get("results", [])
        return results[0]["id"] if results else None

    async def upsert_lead(self, lead: LeadSync, key: str) -> str:
        first, _, last = (lead.name or "").partition(" ")
        props = {
            k: v
            for k, v in {
                "email": lead.email,
                "firstname": first or None,
                "lastname": last or None,
                "company": lead.company,
                "phone": lead.phone,
                "hs_lead_status": _STATUS.get(lead.status, "NEW"),
            }.items()
            if v
        }

        contact_id = await self._find_contact(lead.email)
        if contact_id:
            r = await self._client.patch(
                f"/crm/v3/objects/contacts/{contact_id}", json={"properties": props}
            )
        else:
            r = await self._client.post(
                "/crm/v3/objects/contacts", json={"properties": {**props, "lifecyclestage": "lead"}}
            )
        _raise_for(r)
        contact_id = contact_id or r.json()["id"]

        r = await self._client.post(
            "/crm/v3/objects/notes",
            json={
                "properties": {
                    "hs_timestamp": datetime.now(UTC).isoformat(),
                    "hs_note_body": f"{lead.summary}\n\n(ref {key[:12]})",
                },
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": NOTE_TO_CONTACT,
                            }
                        ],
                    }
                ],
            },
        )
        _raise_for(r)
        return f"hubspot:contact:{contact_id}"
