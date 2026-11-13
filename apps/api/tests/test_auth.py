import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from assistant import auth
from assistant.settings import get_settings

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def staff_token(roles=("consultant",), aud=None, iss=None, exp_in=300, key=KEY):
    s = get_settings()
    return jwt.encode(
        {
            "sub": "u-1",
            "preferred_username": "cora",
            "iss": iss or s.oidc_issuer,
            "aud": aud or s.oidc_audience,
            "exp": int(time.time()) + exp_in,
            "realm_access": {"roles": [*roles, "offline_access"]},
        },
        key,
        algorithm="RS256",
    )


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    async def _key(token):
        return KEY.public_key()

    monkeypatch.setattr(auth, "_signing_key", _key)


async def test_valid_staff_token():
    user = await auth.verify_staff_token(staff_token(("manager",)))
    assert user.sub == "u-1" and user.roles == frozenset({"manager"})  # unknown roles dropped


@pytest.mark.parametrize(
    "kwargs",
    [
        {"aud": "other-api"},
        {"iss": "http://evil/realms/x"},
        {"exp_in": -10},
        {"key": rsa.generate_private_key(public_exponent=65537, key_size=2048)},
    ],
)
async def test_invalid_staff_tokens_rejected(kwargs):
    with pytest.raises(HTTPException) as ei:
        await auth.verify_staff_token(staff_token(**kwargs))
    assert ei.value.status_code == 401


async def test_require_roles_forbids_missing_role():
    dep = auth.require_roles("admin")
    user = await auth.verify_staff_token(staff_token(("consultant",)))
    with pytest.raises(HTTPException) as ei:
        await dep(user)
    assert ei.value.status_code == 403
    assert await auth.require_roles("admin", "consultant")(user) is user


def test_session_token_roundtrip():
    cid = uuid.uuid4()
    sess = auth.decode_session_token(auth.issue_session_token(cid, "web"))
    assert sess.conversation_id == cid and sess.channel == "web"


def test_session_token_rejects_tampering_and_wrong_type():
    tok = auth.issue_session_token(uuid.uuid4(), "web")
    with pytest.raises(HTTPException):
        auth.decode_session_token(tok[:-2] + "xx")
    other = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "other", "exp": time.time() + 60},
        get_settings().app_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException):
        auth.decode_session_token(other)
