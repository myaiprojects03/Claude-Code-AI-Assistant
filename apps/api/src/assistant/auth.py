"""Two kinds of caller:

* Staff: Keycloak-issued RS256 access tokens, verified against the realm JWKS. Realm roles
  (admin, manager, consultant) drive role-based access.
* Website visitors: an anonymous, short-lived HS256 session token bound to one conversation.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from assistant.settings import get_settings

ROLES = ("admin", "manager", "consultant")
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class StaffUser:
    sub: str
    username: str | None
    email: str | None
    roles: frozenset[str]

    def has_any(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


@dataclass(frozen=True)
class ChatSession:
    conversation_id: uuid.UUID
    channel: str


def _unauthorized(detail: str = "invalid or missing token") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


# ---------- Staff (Keycloak) ----------


@lru_cache
def _jwk_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{issuer}/protocol/openid-connect/certs", cache_keys=True, lifespan=3600
    )


async def _signing_key(token: str) -> object:
    client = _jwk_client(get_settings().oidc_issuer)
    # PyJWKClient uses blocking urllib; keep it off the event loop.
    return (await asyncio.to_thread(client.get_signing_key_from_jwt, token)).key


async def verify_staff_token(token: str) -> StaffUser:
    s = get_settings()
    try:
        key = await _signing_key(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=s.oidc_audience,
            issuer=s.oidc_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
        raise _unauthorized() from e
    roles = frozenset(r for r in claims.get("realm_access", {}).get("roles", []) if r in ROLES)
    return StaffUser(
        sub=claims["sub"],
        username=claims.get("preferred_username"),
        email=claims.get("email"),
        roles=roles,
    )


async def get_current_staff(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> StaffUser:
    if creds is None:
        raise _unauthorized()
    return await verify_staff_token(creds.credentials)


def require_roles(*roles: str):
    async def dep(user: StaffUser = Depends(get_current_staff)) -> StaffUser:
        if not user.has_any(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return dep


# ---------- Visitor chat sessions ----------

_SESSION_TYP = "chat_session"


def issue_session_token(conversation_id: uuid.UUID, channel: str) -> str:
    s = get_settings()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(conversation_id),
            "typ": _SESSION_TYP,
            "ch": channel,
            "iat": now,
            "exp": now + s.session_token_ttl_s,
        },
        s.app_secret,
        algorithm="HS256",
    )


def decode_session_token(token: str) -> ChatSession:
    try:
        claims = jwt.decode(
            token,
            get_settings().app_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
        if claims.get("typ") != _SESSION_TYP:
            raise jwt.InvalidTokenError("wrong token type")
        return ChatSession(conversation_id=uuid.UUID(claims["sub"]), channel=claims["ch"])
    except (jwt.PyJWTError, ValueError, KeyError) as e:
        raise _unauthorized("invalid or expired chat session") from e


async def get_chat_session(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ChatSession:
    if creds is None:
        raise _unauthorized()
    return decode_session_token(creds.credentials)
