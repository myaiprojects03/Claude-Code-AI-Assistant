"""Ticketing adapters.

Our `tickets` table always decides *who owns* a ticket, so a client can only see their own
company's tickets whatever the backend. When Jira is connected it mirrors each ticket:
creation and comments go through the outbox; status reads are live (read-only).

* LocalTickets: our DB is the ticketing system (default; free).
* JiraTickets: Jira Cloud / Jira Service Management Free (REST v3, email + API token).
"""

import base64
from typing import Protocol

import httpx

from assistant.outbox.errors import PermanentError


class TicketAdapter(Protocol):
    name: str

    async def create(
        self, *, ref: str, summary: str, description: str, priority: str, company: str
    ) -> str | None: ...

    async def status(self, external_id: str) -> str | None: ...

    async def comment(self, external_id: str, text: str) -> None: ...


class LocalTickets:
    name = "local"

    async def create(self, **_) -> str | None:
        return None  # no external system: the local ref is the ticket

    async def status(self, external_id: str) -> str | None:
        return None

    async def comment(self, external_id: str, text: str) -> None:
        return None


def adf(text: str) -> dict:
    """Atlassian Document Format: one paragraph per line."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": line}]}
            if line
            else {"type": "paragraph", "content": []}
            for line in text.splitlines() or [""]
        ],
    }


_PRIORITY = {"low": "Low", "normal": "Medium", "high": "High", "urgent": "Highest"}


def _raise_for(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    if r.status_code in (400, 401, 403, 404):
        raise PermanentError(f"jira {r.status_code}: {r.text[:300]}")
    r.raise_for_status()


class JiraTickets:
    name = "jira"

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        project_key: str,
        client: httpx.AsyncClient | None = None,
        *,
        issue_type: str = "Task",
    ):
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.project_key = project_key
        self.issue_type = issue_type
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15)
        # Always attach auth, including on an injected client.
        self._client.headers.update(
            {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        )

    async def _find_by_label(self, label: str) -> str | None:
        r = await self._client.post(
            "/rest/api/3/search/jql",
            json={
                "jql": f'project = "{self.project_key}" AND labels = "{label}"',
                "fields": ["key"],
                "maxResults": 1,
            },
        )
        _raise_for(r)
        issues = r.json().get("issues", [])
        return issues[0]["key"] if issues else None

    async def create(
        self, *, ref: str, summary: str, description: str, priority: str, company: str
    ) -> str | None:
        label = f"assistant-{ref.lower()}"
        # Idempotent: a retry after a crash finds the issue created by the earlier attempt.
        if existing := await self._find_by_label(label):
            return existing
        r = await self._client.post(
            "/rest/api/3/issue",
            json={
                "fields": {
                    "project": {"key": self.project_key},
                    "issuetype": {"name": self.issue_type},
                    "summary": f"[{company}] {summary}"[:250],
                    "description": adf(
                        f"{description}\n\nRaised via the website assistant ({ref})."
                    ),
                    "labels": [label],
                    "priority": {"name": _PRIORITY.get(priority, "Medium")},
                }
            },
        )
        if r.status_code == 400 and "priority" in r.text:  # project without a priority field
            r = await self._client.post(
                "/rest/api/3/issue",
                json={
                    "fields": {
                        "project": {"key": self.project_key},
                        "issuetype": {"name": self.issue_type},
                        "summary": f"[{company}] {summary}"[:250],
                        "description": adf(description),
                        "labels": [label],
                    }
                },
            )
        _raise_for(r)
        return r.json()["key"]

    async def status(self, external_id: str) -> str | None:
        r = await self._client.get(f"/rest/api/3/issue/{external_id}", params={"fields": "status"})
        if r.status_code == 404:
            return None
        _raise_for(r)
        return r.json()["fields"]["status"]["name"]

    async def comment(self, external_id: str, text: str) -> None:
        r = await self._client.post(
            f"/rest/api/3/issue/{external_id}/comment", json={"body": adf(text)}
        )
        _raise_for(r)
