"""Calendar adapters. Called from the booking tool (busy times) and the outbox worker (events).

* LocalCalendar: our `bookings` table is the calendar (default; free). Overlaps are already
  impossible thanks to the DB exclusion constraint.
* GoogleCalendar: a free Google Cloud service account with each consultant's calendar shared
  to it. Uses the REST API directly (no SDK). The event id is derived from the booking's
  idempotency key, so a retried insert returns 409 and is treated as success.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import httpx
import jwt

from assistant.outbox.errors import PermanentError

GOOGLE_API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar"


@dataclass
class EventSpec:
    event_id: str  # lowercase hex: valid Google event id (base32hex alphabet)
    calendar_id: str
    start: datetime
    end: datetime
    summary: str
    description: str


class CalendarAdapter(Protocol):
    name: str

    async def busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]: ...

    async def create_event(self, event: EventSpec) -> str: ...

    async def cancel_event(self, calendar_id: str, event_ref: str) -> None: ...


class LocalCalendar:
    name = "local"

    async def busy(self, calendar_id, start, end):
        return []

    async def create_event(self, event: EventSpec) -> str:
        return f"local:{event.event_id[:16]}"

    async def cancel_event(self, calendar_id: str, event_ref: str) -> None:
        return None


class GoogleCalendar:
    name = "google"

    def __init__(self, service_account_file: str, client: httpx.AsyncClient | None = None):
        self._sa = json.loads(Path(service_account_file).read_text(encoding="utf-8"))
        self._client = client or httpx.AsyncClient(timeout=15)
        self._token: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token and self._token[1] - 60 > time.time():
                return self._token[0]
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": self._sa["client_email"],
                    "scope": SCOPE,
                    "aud": self._sa["token_uri"],
                    "iat": now,
                    "exp": now + 3600,
                },
                self._sa["private_key"],
                algorithm="RS256",
            )
            r = await self._client.post(
                self._sa["token_uri"],
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            if r.status_code in (400, 401, 403):
                raise PermanentError(f"google auth failed: {r.text[:200]}")
            r.raise_for_status()
            body = r.json()
            self._token = (body["access_token"], time.time() + body.get("expires_in", 3600))
            return self._token[0]

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}"}

    async def busy(self, calendar_id, start, end):
        r = await self._client.post(
            f"{GOOGLE_API}/freeBusy",
            headers=await self._headers(),
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": calendar_id}],
            },
        )
        r.raise_for_status()
        cal = r.json().get("calendars", {}).get(calendar_id, {})
        if cal.get("errors"):
            raise PermanentError(f"calendar {calendar_id} not accessible: {cal['errors']}")
        return [
            (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in cal.get("busy", [])
        ]

    async def create_event(self, event: EventSpec) -> str:
        r = await self._client.post(
            f"{GOOGLE_API}/calendars/{event.calendar_id}/events",
            headers=await self._headers(),
            json={
                "id": event.event_id,
                "summary": event.summary,
                "description": event.description,
                "start": {"dateTime": event.start.isoformat()},
                "end": {"dateTime": event.end.isoformat()},
            },
        )
        if r.status_code == 409:  # already created by an earlier attempt: idempotent success
            return f"google:{event.event_id}"
        if r.status_code in (400, 403, 404):
            raise PermanentError(f"google rejected event: {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        return f"google:{r.json()['id']}"

    async def cancel_event(self, calendar_id: str, event_ref: str) -> None:
        event_id = event_ref.split(":", 1)[-1]
        r = await self._client.delete(
            f"{GOOGLE_API}/calendars/{calendar_id}/events/{event_id}",
            headers=await self._headers(),
        )
        if r.status_code in (404, 410):  # already gone: idempotent success
            return
        r.raise_for_status()
