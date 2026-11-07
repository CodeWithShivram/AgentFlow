"""Argon2id password hashing. Hashing runs in a worker thread so it never blocks the event loop."""

from functools import lru_cache

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults: Argon2id, RFC 9106 low-memory profile (t=3, m=64 MiB, p=4).
_hasher = PasswordHasher()


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(_hasher.hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def verify_password(password_hash: str, password: str) -> bool:
    return await anyio.to_thread.run_sync(_verify, password_hash, password)


def needs_rehash(password_hash: str) -> bool:
    """True when the hash uses weaker parameters than the current policy."""
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("agentflow-timing-equalizer")


async def spend_verification_time(password: str) -> None:
    """Verifies against a dummy hash so unknown emails take as long as wrong passwords."""
    dummy = await anyio.to_thread.run_sync(_dummy_hash)
    await verify_password(dummy, password)
