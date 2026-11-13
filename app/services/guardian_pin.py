"""보호자 비밀번호 (숫자 4자리) — pt_docs 20 3장 「기능 드러내기」, 2026-11-06.

비밀번호는 해시만 둔다 (PBKDF2-SHA256, 계정마다 다른 소금). 네 자리라 해시만으로는 막지 못하므로
서버가 틀린 횟수를 센다 — 연달아 다섯 번 틀리면 5분 동안 받지 않는다.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from app.core.auth import aware
from app.models import User
from app.models.mixins import utcnow

MAX_FAILURES = 5
LOCK_SECONDS = 300
_ITER = 120_000


def make_hash(pin: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITER).hex()
    return f"pbkdf2_sha256${_ITER}${salt}${digest}"


def matches(stored: str, pin: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(iters)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(got, digest)


def locked_seconds(user: User) -> int:
    """막힌 동안이면 남은 초, 아니면 0."""

    if user.guardian_pin_locked_until is None:
        return 0
    left = (aware(user.guardian_pin_locked_until) - utcnow()).total_seconds()
    return max(0, int(left + 0.999))


def check(user: User, pin: str) -> bool | None:
    """맞으면 True(틀린 횟수를 지운다), 틀리면 False(세고, 다섯 번째면 잠근다). 막힌 동안이면 None.
    부른 쪽이 커밋한다."""

    if locked_seconds(user):
        return None
    if user.guardian_pin_hash and matches(user.guardian_pin_hash, pin):
        user.guardian_pin_failures = 0
        user.guardian_pin_locked_until = None
        return True
    user.guardian_pin_failures = (user.guardian_pin_failures or 0) + 1
    if user.guardian_pin_failures >= MAX_FAILURES:
        user.guardian_pin_failures = 0
        user.guardian_pin_locked_until = utcnow() + timedelta(seconds=LOCK_SECONDS)
    return False


def tries_left(user: User) -> int:
    return MAX_FAILURES - (user.guardian_pin_failures or 0)
