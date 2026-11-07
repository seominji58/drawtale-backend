"""카드 이야기 음성 미리 만들기 — 나레이션 판만 (2026-10-29).

**쓰지 않기로 했다 (2026-10-29 사용자 「비용 감당 안 돼」).** 음성은 시연이든 무엇이든 요청할 때 만들고,
같은 조합이 다시 오면 저장해 둔 것을 쓴다(jobs.cached_page_audio). 이 스크립트로 만든 긴 나레이션 56편은 그대로 쓴다.

나레이션 판은 이름 · 주인공 목소리와 상관없이 조합마다 음성이 하나다(jobs.card_audio_key).
서비스가 처음 요청 때 만들어 두는 것과 같은 자리 · 같은 모양으로 미리 만든다. 이미 있으면 건너뛴다.
캐릭터 목소리 판은 이름마다 달라서 미리 만들지 않는다.

    python -m scripts.pregen_card_audio --length long --dry-run          # 몇 편 · 몇 자인지만
    python -m scripts.pregen_card_audio --length long --budget 20000     # 이번에 많아야 20,000 자 쓰기

ElevenLabs 크레딧을 쓴다 — 허락받고. 계정 조회의 남은 양은 수십 분 늦게 따라온다(2026-10-29 에 2만 자 넘게
늦음) — 그래서 멈출 곳은 이번에 쓸 글자 수(--budget)로 직접 센다. 계정 조회는 처음 한 번 참고로만 보여 준다.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Story
from app.models.card_story import CardStory
from app.services import card_stories as cs
from app.services.jobs import card_audio_key
from app.services.storage import get_storage
from app.services.tts import ElevenLabsSpeech, get_tts

# 순서: 장소 · 문제마다 고르게 돌도록 (끝까지 못 가도 장소마다 몇 편씩은 있게)
ORDER = {c: i for i, c in enumerate(cs.combos())}


def remaining() -> int:
    s = get_settings()
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": s.elevenlabs_api_key}
    )
    d = json.load(urllib.request.urlopen(req, timeout=20))
    return d["character_limit"] - d["character_count"]


def spread(rows: list[CardStory]) -> list[CardStory]:
    """(장소, 문제) 묶음마다 한 편씩 돌아가며 뽑는다."""

    groups: dict[tuple[str, str], list[CardStory]] = {}
    for r in sorted(rows, key=lambda r: ORDER.get(cs.Combo(r.place, r.problem, r.action, r.result), 0)):
        groups.setdefault((r.place, r.problem), []).append(r)
    out, i = [], 0
    while len(out) < len(rows):
        for g in groups.values():
            if i < len(g):
                out.append(g[i])
        i += 1
    return out


def _has(storage, path: str) -> bool:
    try:
        return bool(storage.read(path))
    except Exception:
        return False


def pages_of(row: CardStory) -> list[tuple[str, str]]:
    """[(저장 경로 끝, 읽을 글)] — 서비스의 cached_page_audio · cached_audio 와 같은 이름."""

    if row.length == "long":
        book = cs.book_from_payload(row.payload, None)
        return [(f"page{p.page:02d}.mp3", p.text()) for p in book.pages]
    return [("full.mp3", row.payload["text"])]


def speak(tts, text: str, tries: int = 6) -> bytes:
    """429(동시 요청 한도)면 조금 기다렸다 다시."""

    for i in range(tries):
        try:
            return tts.speak(text)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise AssertionError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", choices=["long", "short"], required=True)
    ap.add_argument("--budget", type=int, required=True, help="이번에 쓸 크레딧(자) 상한")
    ap.add_argument("--limit", type=int, default=0, help="많아야 몇 편 (0 이면 크레딧이 허락하는 만큼)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    storage = get_storage()
    with SessionLocal() as db:
        rows = (
            db.query(CardStory)
            .filter_by(length=a.length, voice_mode="narration", status="approved")
            .all()
        )
        todo = []
        for r in spread(rows):
            key = card_audio_key(r, Story(length=r.length, voice_mode="narration"))
            names = [n for n, _ in pages_of(r)] + ["cast.json"]
            if not all(_has(storage, f"{key}/{n}") for n in names):
                todo.append((r, key))

    chars = [sum(len(t) for _, t in pages_of(r)) for r, _ in todo]
    left = remaining()
    print(f"{a.length} 나레이션: 만들 것 {len(todo)}편 · {sum(chars):,}자, 남은 크레딧 {left:,}자, 이번 상한 {a.budget:,}자 (계정 조회는 늦게 따라옴)")
    if a.dry_run:
        return

    tts = get_tts()
    assert isinstance(tts, ElevenLabsSpeech), "ElevenLabs 설정이 아니다"
    done = spent = 0
    for (r, key), n in zip(todo, chars, strict=True):
        if spent + n > a.budget or (a.limit and done >= a.limit):
            break
        # 쪽마다 만들자마자 둔다 — 끊겨도 다음에 없는 쪽만 만든다. cast.json 은 다 된 뒤에 둬서
        # 서비스는 다 갖춰진 편만 쓴다 (cached_page_audio)
        need = [(name, text) for name, text in pages_of(r) if not _has(storage, f"{key}/{name}")]
        with ThreadPoolExecutor(2) as ex:  # Starter 동시 요청 한도에 걸리지 않게 둘씩
            list(ex.map(lambda p, k=key: storage.save(f"{k}/{p[0]}", speak(tts, p[1])), need))
        storage.save(f"{key}/cast.json", b"{}")
        n = sum(len(t) for _, t in need)
        done, spent = done + 1, spent + n
        print(f"  {done}. {r.place} · {r.problem} · {r.action} · {r.result} ({n}자, 이번에 쓴 것 {spent:,})", flush=True)
    print(f"끝: {done}편 · {spent:,}자, 못 만든 것 {len(todo) - done}편")


if __name__ == "__main__":
    main()
