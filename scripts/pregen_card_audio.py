"""카드 이야기 음성 미리 만들기 — 나레이션 판만 (2026-10-29).

나레이션 판은 이름 · 주인공 목소리와 상관없이 조합마다 음성이 하나다(jobs.card_audio_key).
서비스가 처음 요청 때 만들어 두는 것과 같은 자리 · 같은 모양으로 미리 만든다. 이미 있으면 건너뛴다.
캐릭터 목소리 판은 이름마다 달라서 미리 만들지 않는다.

    python -m scripts.pregen_card_audio --length long --dry-run          # 몇 편 · 몇 자인지만
    python -m scripts.pregen_card_audio --length long --keep 5000        # 남은 크레딧이 5,000 자가 될 때까지

ElevenLabs 크레딧을 쓴다 — 허락받고. 남은 양은 계정 조회(무료)로 한 편마다 확인한다.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", choices=["long", "short"], required=True)
    ap.add_argument("--keep", type=int, default=5000, help="남겨 둘 크레딧(자)")
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
    print(f"{a.length} 나레이션: 만들 것 {len(todo)}편 · {sum(chars):,}자, 남은 크레딧 {left:,}자, 남길 것 {a.keep:,}자")
    if a.dry_run:
        return

    tts = get_tts()
    assert isinstance(tts, ElevenLabsSpeech), "ElevenLabs 설정이 아니다"
    done = spent = 0
    start = left
    for (r, key), n in zip(todo, chars, strict=True):
        if left - n < a.keep or (a.limit and done >= a.limit):
            break
        with ThreadPoolExecutor(3) as ex:  # 서비스와 같이 쪽 셋씩
            audio = list(ex.map(lambda p: (p[0], tts.speak(p[1])), pages_of(r)))
        for name, data in audio:  # 모두 만든 뒤에만 둔다 — 한 쪽이라도 실패하면 이 편은 두지 않는다
            storage.save(f"{key}/{name}", data)
        storage.save(f"{key}/cast.json", b"{}")
        done, spent = done + 1, spent + n
        # 계정 조회는 늦게 반영될 때가 있어 직접 센 것과 작은 쪽을 쓴다
        left = min(remaining(), start - spent)
        print(f"  {done}. {r.place} · {r.problem} · {r.action} · {r.result} ({n}자, 남은 {left:,})", flush=True)
    print(f"끝: {done}편 · {spent:,}자, 남은 크레딧 {left:,}자, 못 만든 것 {len(todo) - done}편")


if __name__ == "__main__":
    main()
