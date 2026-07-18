"""Password hashing and opaque-token helpers.

Argon2id is used directly rather than through a wrapper: the parameters live in
the encoded hash, so a later increase to the cost settings re-hashes on the next
successful login without invalidating anybody's password.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from agent.config import get_settings
from agent.observability.logger import get_logger

log = get_logger(__name__)

#: Cost parameters come from configuration, so the process builds one hasher.
#: ``argon2-cffi`` reads them at construction and embeds them in every hash.
_hasher: PasswordHasher | None = None


def get_hasher() -> PasswordHasher:
    global _hasher
    if _hasher is None:
        settings = get_settings()
        _hasher = PasswordHasher(
            time_cost=settings.ARGON2_TIME_COST,
            memory_cost=settings.ARGON2_MEMORY_COST_KIB,
            parallelism=settings.ARGON2_PARALLELISM,
        )
    return _hasher


def hash_password(password: str) -> str:
    """Hash a password with Argon2id and the deployment pepper.

    The pepper is appended to the password rather than combined afterwards, so
    the result is still a single ordinary Argon2 hash and can be verified with
    stock tooling given the same secret.
    """
    settings = get_settings()
    material = f"{password}{settings.CREDENTIAL_PEPPER}"
    return get_hasher().hash(material)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash. Never raises."""
    settings = get_settings()
    material = f"{password}{settings.CREDENTIAL_PEPPER}"
    try:
        return bool(get_hasher().verify(password_hash, material))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when a hash was made with weaker parameters than we now want.

    Checked on every successful login so raising the cost settings takes effect
    gradually instead of requiring a password reset campaign.
    """
    try:
        return get_hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_token(nbytes: int = 32) -> str:
    """A URL-safe random token for refresh, reset and invite flows."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Hash an opaque token for storage.

    These tokens are already 256 bits of entropy, so a plain SHA-256 is the
    right primitive — there is nothing to brute-force, and Argon2's cost would
    only make every authenticated request slower. The comparison in
    :func:`constant_time_equals` is what protects them at lookup time.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking their contents through timing."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def constant_time_prefix_match(candidate: str, expected: str) -> bool:
    """Prefix comparison used by the API key lookup.

    API keys are stored hashed, so the lookup is by hash, not by prefix; this
    exists for the one place a caller-supplied prefix is compared against a
    stored one (showing which key a failed attempt was aiming at).
    """
    return hmac.compare_digest(candidate[: len(expected)], expected)
