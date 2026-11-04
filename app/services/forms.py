"""형태 — 그림의 몸 구조 (2족형 · 4족형 …, pt_docs 19).

형태 정의의 원본은 AI 서버(drawtale-ai runtime/ai_server/forms/*.json)다. 여기서는 받아 두고 쓴다 —
관절 검사(형태마다 관절 이름이 다르다), 쪽 동작 고르기(형태가 할 수 있는 동작만), 화면용 GET /api/v1/forms.
AI 에서 못 받으면 저장소에 둔 2족형 사본(app/data/forms/biped.json)만으로 지금처럼 돈다 (19 3-1 결정 ①).
"""

import json
import logging
import threading
import time
from pathlib import Path

from app.core.errors import AppError

log = logging.getLogger(__name__)

DEFAULT = "biped"
_BIPED = json.loads((Path(__file__).resolve().parents[1] / "data" / "forms" / "biped.json").read_text(encoding="utf-8"))
# 받아 둔 형태를 다시 묻는 간격. 형태는 배포할 때만 바뀐다
TTL_SECONDS = 600

_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_fetched_at = 0.0


def all_forms() -> dict[str, dict]:
    """형태 id → 정의. AI 에서 받은 것 (못 받으면 2족형 사본만)."""
    global _cache, _fetched_at
    with _lock:
        if _cache is not None and time.monotonic() - _fetched_at < TTL_SECONDS:
            return _cache
    from app.services.ai_client import get_ai_client   # 순환 import 를 피한다

    try:
        got = {f["id"]: f for f in get_ai_client().forms()}
    except Exception:  # noqa: BLE001 — AI 가 꺼져 있어도 사람 그림은 돈다
        log.warning("형태 목록을 AI 에서 받지 못해 2족형만 씁니다", exc_info=True)
        got = {}
    fetched = bool(got)
    got.setdefault(DEFAULT, _BIPED)
    with _lock:
        _cache = got
        # 못 받았으면 30초 뒤에 다시 묻는다 (AI 가 막 다시 켜지는 중일 수 있다)
        _fetched_at = time.monotonic() if fetched else time.monotonic() - TTL_SECONDS + 30
    return got


def reset() -> None:
    """테스트 · 배포 뒤 다시 받게."""
    global _cache
    with _lock:
        _cache = None


def get(form_id: str | None) -> dict:
    forms = all_forms()
    form = forms.get(form_id or DEFAULT)
    if form is None:
        raise AppError("UNKNOWN_FORM", "알 수 없는 모양이에요.", 422, {"available": sorted(forms)})
    return form


def joint_order(form_id: str | None) -> list[str]:
    """그 형태의 관절 이름 (찍는 순서)."""
    return [j["name"] for j in get(form_id)["joints"]]


def render_motions(form_id: str | None) -> set[str] | None:
    """그 형태가 렌더할 수 있는 동작 이름. 2족형은 None (AI 의 모든 동작)."""
    if (form_id or DEFAULT) == DEFAULT:
        return None
    return set(get(form_id).get("motions") or {})


def public(form: dict) -> dict:
    """화면에 내주는 칸만 — 대응표 · 동작 파일 경로 · 카메라 같은 AI 내부 값은 뺀다."""
    keys = ("id", "label", "group", "kid_label", "examples", "view", "joints", "guide", "hero", "ready")
    return {k: form[k] for k in keys if k in form}
