"""Per-client sliding-window rate limit for the public chat endpoints.

In-process memory: fine for one API instance. With several instances, move the counters to
Postgres or Redis (Phase 7 hardening).
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from assistant.settings import get_settings

_hits: dict[str, deque[float]] = defaultdict(deque)
WINDOW_S = 60.0


def client_key(request: Request) -> str:
    # X-Forwarded-For is only meaningful when our own proxy (e.g. the Cloudflare tunnel)
    # sets it. Otherwise anyone can send a fresh fake value per request and never hit the
    # limit (Phase 7 finding), so by default the connection's own address is used.
    if get_settings().trust_proxy_headers and (fwd := request.headers.get("x-forwarded-for")):
        return fwd.split(",")[-1].strip()  # the hop our proxy appended, not the client's
    return request.client.host if request.client else "unknown"


def _check(key: str, limit: int) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        retry = int(WINDOW_S - (now - q[0])) + 1
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many messages, slow down",
            headers={"Retry-After": str(retry)},
        )
    q.append(now)


async def limit_public(request: Request) -> None:
    _check(f"ip:{client_key(request)}", get_settings().rate_limit_per_minute)


def limit_staff(sub: str) -> None:
    """Knowledge-assistant questions per staff member: each runs the local model for up to
    a minute, so a loop (or a stolen token) could otherwise tie the machine up."""
    _check(f"staff:{sub}", get_settings().internal_rate_limit_per_minute)


def reset() -> None:
    _hits.clear()
