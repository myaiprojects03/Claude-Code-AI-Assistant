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
    # Behind a reverse proxy/tunnel, trust only the first X-Forwarded-For hop it sets.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_public(request: Request) -> None:
    limit = get_settings().rate_limit_per_minute
    now = time.monotonic()
    q = _hits[client_key(request)]
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


def reset() -> None:
    _hits.clear()
