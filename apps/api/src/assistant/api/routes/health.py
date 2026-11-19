import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db import get_session
from assistant.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:  # report, don't crash
        response.status_code = 503
        return {"status": "unavailable", "db": type(e).__name__}
    return {"status": "ready", "db": "ok"}


LOCAL = {"127.0.0.1", "::1", "localhost"}


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus scrape. Metrics reveal traffic, error rates and route names, so outside
    dev they're only served to this machine or to a scraper presenting METRICS_TOKEN."""
    s = get_settings()
    if s.app_env != "dev":
        auth = request.headers.get("authorization", "")
        token_ok = bool(s.metrics_token) and hmac.compare_digest(
            auth.encode(), f"Bearer {s.metrics_token}".encode()
        )
        local = (request.client.host if request.client else "") in LOCAL
        if not (token_ok or local):
            raise HTTPException(404, "Not Found")  # don't advertise that it exists
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
