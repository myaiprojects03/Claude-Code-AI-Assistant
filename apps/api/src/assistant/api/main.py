import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from assistant import alerts, metrics
from assistant.api.routes import admin, chat, health
from assistant.db import dispose_engine, init_engine
from assistant.kb.embeddings import Embedder, build_embedder
from assistant.llm.base import LLMProvider
from assistant.llm.factory import build_chain
from assistant.logs import configure_logging
from assistant.orchestrator import Orchestrator
from assistant.settings import get_settings
from assistant.tools import ToolRegistry, build_tools

log = structlog.get_logger()


def create_app(
    *,
    llm: LLMProvider | None = None,
    internal_llm: LLMProvider | None = None,
    tools: ToolRegistry | None = None,
    embedder: Embedder | None = None,
    database_url: str | None = None,
) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(json=settings.app_env != "dev")
        init_engine(database_url)
        app.state.embedder = embedder or build_embedder()
        public_llm = llm or build_chain()
        # Internal (confidential) chats default to the local chain; a supplied test model is
        # used for both so tests never need Ollama.
        private_llm = internal_llm or (
            llm if llm is not None else build_chain(settings.internal_llm_chain_list)
        )
        app.state.orchestrator = Orchestrator(
            public_llm,
            tools or ToolRegistry(build_tools(app.state.embedder)),
            internal_llm=private_llm,
        )
        log.info(
            "api.started", env=settings.app_env, llm=public_llm.name, internal_llm=private_llm.name
        )
        yield
        await dispose_engine()

    app = FastAPI(title="AI Assistant API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        route = request.url.path
        try:
            response = await call_next(request)
        except Exception as e:
            log.exception("http.unhandled", path=route)
            await alerts.send_alert(
                "critical",
                "Unhandled API error",
                path=route,
                error=type(e).__name__,
                request_id=request_id,
            )
            response = JSONResponse(
                {"detail": "internal error", "request_id": request_id}, status_code=500
            )
        # Use the matched route template (not raw path) to keep metric cardinality low.
        if (r := request.scope.get("route")) is not None:
            route = getattr(r, "path", route)
        metrics.HTTP_REQUESTS.labels(request.method, route, response.status_code).inc()
        metrics.HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - started)
        response.headers["x-request-id"] = request_id
        return response

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(admin.router)
    return app


app = create_app()
