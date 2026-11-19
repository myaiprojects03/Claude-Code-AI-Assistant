"""HTTP hardening (Phase 7): security headers and a request size limit.

Both are plain ASGI middleware so they also cover error responses and streaming bodies.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# This API only serves JSON (and the dev-only docs page): nothing may frame it, sniff it,
# or leak its URLs; and responses with personal data must not be cached by browsers.
BASE_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
    b"cross-origin-opener-policy": b"same-origin",
}
API_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
HSTS = b"max-age=31536000; includeSubDomains"


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, https: bool):
        self.app, self.https = app, https

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in BASE_HEADERS
                ]
                headers += list(BASE_HEADERS.items())
                if not path.startswith(("/docs", "/redoc")):
                    headers.append((b"content-security-policy", API_CSP))
                if path.startswith("/v1/"):
                    headers.append((b"cache-control", b"no-store"))
                if self.https:
                    headers.append((b"strict-transport-security", HSTS))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimit:
    """Refuse request bodies over `max_bytes` with 413, whether or not Content-Length is
    sent. Without a length (chunked), the body is read here first, at most max_bytes of it,
    then handed on; our bodies are small JSON, so buffering costs nothing."""

    def __init__(self, app: ASGIApp, *, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = None
        for k, v in scope.get("headers", []):
            if k == b"content-length" and v.isdigit():
                length = int(v)
        if length is not None:
            if length > self.max_bytes:
                return await _too_large(send)
            return await self.app(scope, receive, send)

        chunks, seen = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # client went away
                return await self.app(scope, _replay([message], receive), send)
            body = message.get("body", b"")
            seen += len(body)
            if seen > self.max_bytes:
                return await _too_large(send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        whole = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        await self.app(scope, _replay([whole], receive), send)


def _replay(messages: list[Message], then: Receive) -> Receive:
    """Hand back the buffered body, then the real channel (which reports a disconnect)."""
    pending = list(messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return await then()

    return receive


async def _too_large(send: Send) -> None:
    body = b'{"detail":"request too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
