"""돈과 CPU 를 쓰는 요청의 횟수 · 동시 실행 제한 (F-NFR-10, 03 기능 명세).

조회와 작업 상태 묻기는 막지 않는다. 비회원은 IP 로 세는데, 교실은 IP 하나를 여럿이 쓰므로
조회까지 막으면 기다리는 화면이 멈춘다.

uvicorn 프로세스 하나로 도는 전제라 메모리에 둔다. 서버를 다시 켜면 처음부터 다시 센다.
"""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import User

WINDOW_SECONDS = 60
PER_MINUTE = {"analyze": 5, "story": 3, "voice": 3, "login": 5}
# 비회원은 사람을 알 수 없어 IP 로 센다. 교실에서 함께 쓰므로 그만큼 넉넉히 둔다
GUEST_FACTOR = 4
BUSY_RETRY_SECONDS = 5

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_busy: set[str] = set()


def reset() -> None:
    with _lock:
        _hits.clear()
        _busy.clear()


def _enabled() -> bool:
    return get_settings().rate_limit_enabled


def _too_many(retry_after: int) -> AppError:
    return AppError(
        "RATE_LIMITED",
        "요청이 너무 잦아요. 잠시 뒤에 다시 해 주세요.",
        429,
        headers={"Retry-After": str(retry_after)},
    )


def check(bucket: str, request: Request, user: User | None = None) -> None:
    """이번 요청을 세고, 1분 기준을 넘으면 429 로 멈춘다."""

    if not _enabled():
        return
    limit = PER_MINUTE[bucket]
    if user is None and bucket != "login":
        limit *= GUEST_FACTOR
    who = str(user.id) if user else (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _lock:
        hits = _hits[(bucket, who)]
        while hits and hits[0] <= now - WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            raise _too_many(max(1, math.ceil(hits[0] + WINDOW_SECONDS - now)))
        hits.append(now)


DAY_SECONDS = 24 * 60 * 60


def daily(bucket: str, request: Request, user: User | None, limit: int) -> None:
    """하루(24시간) 기준 한도 (2026-11-02).

    비회원은 IP, 회원은 계정마다. limit 이 0 이하면 세지 않는다.

    분당 제한만으로는 하루 종일 조금씩 부르는 것을 못 막는다 — 이야기는 유료 API 를 부른다.
    """

    if not _enabled() or limit <= 0:
        return
    who = str(user.id) if user else (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _lock:
        hits = _hits[(f"{bucket}-day", who)]
        while hits and hits[0] <= now - DAY_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            raise _too_many(max(1, math.ceil(hits[0] + DAY_SECONDS - now)))
        hits.append(now)


def claim(key: str | None) -> None:
    """같은 key 로 돌고 있는 작업이 있으면 429. key 가 None 이면 막지 않는다."""

    if key is None or not _enabled():
        return
    with _lock:
        if key in _busy:
            raise _too_many(BUSY_RETRY_SECONDS)
        _busy.add(key)


def release(key: str | None) -> None:
    with _lock:
        _busy.discard(key)


def run_then_release(key: str | None, task: Callable, *args) -> None:
    """백그라운드 작업이 끝나면 성공 · 실패와 상관없이 자리를 비운다."""

    try:
        task(*args)
    finally:
        release(key)
