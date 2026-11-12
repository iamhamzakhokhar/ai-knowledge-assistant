"""Password hashing, opaque credentials, and bounded authentication work."""

import hashlib
import hmac
import re
import secrets
from contextlib import closing
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from redis.exceptions import RedisError

from knowledge_assistant.config import Settings
from knowledge_assistant.queueing import create_redis
from knowledge_assistant.secret_files import SecretFileError

COOKIE_NAME = "aka_session"
SESSION_SECONDS = 8 * 60 * 60
INVITE_SECONDS = 24 * 60 * 60
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{43}$"
_HASH_SLOTS = BoundedSemaphore(2)

# Both limits are charged atomically before a request starts password work.
_AUTH_WINDOW = """
for index, key in ipairs(KEYS) do
    local used = tonumber(redis.call('GET', key) or '0')
    if used >= tonumber(ARGV[index + 1]) then return 0 end
end
for _, key in ipairs(KEYS) do
    if redis.call('INCR', key) == 1 then redis.call('EXPIRE', key, ARGV[1]) end
end
return 1
"""


def check_auth_attempt(request: Request, email: str) -> None:
    ip = request.client.host if request.client else "unknown"
    account_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    address_key = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    try:
        with closing(create_redis(request.app.state.workspace_settings)) as redis:
            allowed = redis.eval(
                _AUTH_WINDOW,
                2,
                f"aka:auth:account:{account_key}",
                f"aka:auth:address:{address_key}",
                900,
                12,
                80,
            )
    except RedisError, SecretFileError, OSError:
        raise HTTPException(503, "Authentication is unavailable") from None
    if allowed != 1:
        raise HTTPException(429, "Too many authentication attempts", headers={"Retry-After": "900"})


def _scrypt(password: str, salt: bytes) -> bytes:
    # OWASP's 32 MiB scrypt profile. Two slots bound memory even across distinct IPs.
    if not _HASH_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "Authentication is busy", headers={"Retry-After": "1"})
    try:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**15, r=8, p=3, dklen=32, maxmem=64 * 1024**2
        )
    finally:
        _HASH_SLOTS.release()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    # A missing account still incurs the same derivation cost.
    try:
        algorithm, salt_hex, digest_hex = (stored or "").split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        valid = algorithm == "scrypt-v1" and len(salt) == 16 and len(expected) == 32
    except ValueError:
        valid = False
    if not valid:
        salt, expected = bytes(16), bytes(32)
    matches = hmac.compare_digest(_scrypt(password, salt), expected)
    return valid and matches


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def session_digest(request: Request) -> bytes | None:
    cookie = request.cookies.get(COOKIE_NAME, "")
    return token_digest(cookie) if re.fullmatch(TOKEN_PATTERN, cookie) else None


def _secure_cookie(request: Request) -> bool:
    settings: Settings = request.app.state.workspace_settings
    origin = request.headers.get("origin", "")
    if settings.app_env != "development" or origin not in settings.allowed_origins:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}


def write_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        path="/",
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, secure=_secure_cookie(request), samesite="lax"
    )
