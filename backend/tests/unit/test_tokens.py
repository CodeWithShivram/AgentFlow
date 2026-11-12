import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from app.core.tokens import (
    ALGORITHM,
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
    hash_token,
    new_opaque_token,
    token_matches,
)
from tests.conftest import UNIT_JWT_KEY, UNIT_JWT_KEY_ID, make_settings

USER_ID = uuid.uuid4()
SESSION_ID = uuid.uuid4()
OTHER_KEY = "another-signing-key-abcdefghijklmnopqrstuvwxyz"


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": "agentflow-api",
        "aud": "agentflow",
        "sub": str(USER_ID),
        "sid": str(SESSION_ID),
        "typ": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": "j",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _sign(claims: dict[str, Any], key: str = UNIT_JWT_KEY, kid: str = UNIT_JWT_KEY_ID) -> str:
    return jwt.encode(claims, key, algorithm=ALGORITHM, headers={"kid": kid})


def test_round_trip_and_no_roles_in_token() -> None:
    settings = make_settings()
    issued = create_access_token(settings, user_id=USER_ID, session_id=SESSION_ID)

    claims = decode_access_token(settings, issued.token)
    raw = jwt.decode(issued.token, options={"verify_signature": False})

    assert (claims.user_id, claims.session_id) == (USER_ID, SESSION_ID)
    assert jwt.get_unverified_header(issued.token) == {
        "alg": "HS256",
        "kid": UNIT_JWT_KEY_ID,
        "typ": "JWT",
    }
    assert "role" not in raw and "roles" not in raw and "workspace_id" not in raw
    assert raw["exp"] - raw["iat"] == settings.auth_access_token_ttl_seconds


def test_expired_token_is_rejected() -> None:
    settings = make_settings()
    past = datetime.now(UTC) - timedelta(hours=1)
    issued = create_access_token(settings, user_id=USER_ID, session_id=SESSION_ID, now=past)

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(settings, issued.token)


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"typ": "refresh"},
        {"sid": None},
        {"sub": "not-a-uuid"},
        {"nbf": timedelta(minutes=5)},
        {"exp": None},
    ],
)
def test_wrong_or_missing_claims_are_rejected(overrides: dict[str, Any]) -> None:
    # Claims are built when the test runs, not at collection: otherwise a slow suite lets the
    # 5-minute `exp` lapse and every case is rejected as expired instead of for its own reason.
    if isinstance(overrides.get("nbf"), timedelta):
        overrides = {**overrides, "nbf": datetime.now(UTC) + overrides["nbf"]}
    claims = _claims(**overrides)
    decode_access_token(make_settings(), _sign(_claims()))  # the baseline token is valid
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(make_settings(), _sign(claims))


def test_tampered_signature_is_rejected() -> None:
    token = _sign(_claims())
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}{'AA' if signature[-2:] != 'AA' else 'BB'}"

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(make_settings(), tampered)


def test_token_signed_with_another_key_is_rejected() -> None:
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(make_settings(), _sign(_claims(), key=OTHER_KEY))


def test_unknown_key_id_is_rejected() -> None:
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(make_settings(), _sign(_claims(), kid="unknown"))


# The forged HS512 token deliberately reuses the 42-byte HS256 key; PyJWT warns about that.
@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_algorithm_none_and_other_algorithms_are_rejected() -> None:
    unsigned = jwt.encode(_claims(), None, algorithm="none", headers={"kid": UNIT_JWT_KEY_ID})
    hs512 = jwt.encode(_claims(), UNIT_JWT_KEY, algorithm="HS512", headers={"kid": UNIT_JWT_KEY_ID})

    for token in (unsigned, hs512, "not-a-jwt", ""):
        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(make_settings(), token)


def test_key_rotation_keeps_old_tokens_valid_until_the_old_key_is_removed() -> None:
    old = make_settings()
    token = create_access_token(old, user_id=USER_ID, session_id=SESSION_ID).token
    rotated = make_settings(
        auth_jwt_signing_keys={UNIT_JWT_KEY_ID: UNIT_JWT_KEY, "k2": OTHER_KEY},
        auth_jwt_active_key_id="k2",
    )
    retired = make_settings(auth_jwt_signing_keys={"k2": OTHER_KEY}, auth_jwt_active_key_id="k2")

    assert decode_access_token(rotated, token).user_id == USER_ID
    new_token = create_access_token(rotated, user_id=USER_ID, session_id=SESSION_ID).token
    assert jwt.get_unverified_header(new_token)["kid"] == "k2"
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(retired, token)


def test_opaque_tokens_are_random_and_only_hashes_compare() -> None:
    tokens = {new_opaque_token() for _ in range(100)}

    assert len(tokens) == 100
    token = tokens.pop()
    assert len(token) >= 43  # 256 bits, base64url
    assert len(hash_token(token)) == 64 and hash_token(token) != token
    assert token_matches(token, hash_token(token))
    assert not token_matches(token + "x", hash_token(token))
    assert not token_matches(None, hash_token(token))
    assert not token_matches("", hash_token(token))
