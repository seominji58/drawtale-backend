"""카드 이야기 미리 만들기 (pt_docs 13 1-1). 사람이 읽기 전 초안을 JSON 으로 남긴다.

    python -m scripts.make_card_stories --pilot              # 시험 8편 (아래 PILOT)
    python -m scripts.make_card_stories --all --workers 4    # 512편 — 비용이 크다, 허락받고
    python -m scripts.make_card_stories --load storage/card_stories/pilot.json   # DB 에 draft 로 넣기

만드는 길은 서비스와 같다 — story_writer.StoryWriter (긴 이야기는 Claude, 짧은 이야기는 gpt-4o-mini,
검열 · 카드 뜻 검사 · 다시 쓰기 그대로). 캐릭터 목소리 판은 자리 이름 「라라」로 쓰고 이름 자리로 바꿔 둔다.
결과는 storage/card_stories/ (git 에 올리지 않는 폴더). 키는 .env 에서 읽는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.services import card_stories as cs
from app.services.book import book_problems
from app.services.story_writer import STORY_MODEL, get_story_writer

OUT = Path("storage/card_stories")
# 지시문을 고치면 올린다 — 저장본이 어느 지시문으로 만든 것인지 남긴다 (13 7장)
PROMPT_VERSION = "2026-10-28"

# 시험 8편: 문제마다 2편, 장소 · 행동 · 결과가 겹치지 않게. 안전 수칙 카드(그 자리에서 기다렸어요 · 어른에게 물어봤어요)와
# 「숨었어요」(주어 지키기), 「모두 기뻐했어요」를 넣었다
PILOT = [
    cs.Combo("집", "넘어졌어요", "툭툭 털고 달려갔어요", "다시 씩씩해졌어요"),
    cs.Combo("우주", "넘어졌어요", "어른을 불렀어요", "웃음이 났어요"),
    cs.Combo("놀이터", "친구가 울고 있었어요", "다가가서 도와줬어요", "친구가 웃었어요"),
    cs.Combo("바다", "친구가 울고 있었어요", "옆에서 기다려 줬어요", "모두 기뻐했어요"),
    cs.Combo("숲", "길을 잃었어요", "그 자리에서 기다렸어요", "길을 찾았어요"),
    cs.Combo("마을", "길을 잃었어요", "어른에게 물어봤어요", "무사히 돌아왔어요"),
    cs.Combo("동굴", "어두워졌어요", "숨었어요", "해가 떴어요"),
    cs.Combo("학교", "어두워졌어요", "살금살금 걸어갔어요", "잠이 들었어요"),
]
# 시험에서 캐릭터 목소리 판(이름 자리)까지 볼 조합
PILOT_CHARACTERS = PILOT[2:3] + PILOT[6:7]


def make_one(writer, combo: cs.Combo, length: str, mode: str) -> dict:
    s = get_settings()
    choices = {"place": combo.place, "problem": combo.problem, "action": combo.action, "result": combo.result}
    characters = mode == "characters"
    name = cs.SLOT_NAME if characters else None
    t = time.time()
    problems: list[str] = []
    if length == "long":
        book = writer.write_book(choices, set(), s.story_pages, characters, name)
        problems = book_problems(book, choices, s.story_pages, characters)
        payload = cs.book_payload(book)
        model = s.story_book_claude_model if writer.claude is not None and s.story_book_provider == "anthropic" else s.story_book_model
    elif characters:
        script = writer.write_script(combo.place, combo.problem, combo.action, combo.result, name)
        payload = cs.short_payload(sentences=[[(ln.speaker, ln.text, ln.emotion) for ln in parts] for parts in script.sentences], cast=script.cast)
        model = STORY_MODEL
    else:
        payload = cs.short_payload(text=writer.write(combo.place, combo.problem, combo.action, combo.result))
        model = STORY_MODEL
    return {
        "place": combo.place, "problem": combo.problem, "action": combo.action, "result": combo.result,
        "length": length, "voice_mode": mode, "payload": payload, "model": model,
        "prompt_version": PROMPT_VERSION, "problems": problems, "seconds": round(time.time() - t, 1),
    }


def generate(jobs: list[tuple[cs.Combo, str, str]], out: Path, workers: int) -> None:
    writer = get_story_writer()
    if writer is None:
        sys.exit("OPENAI_API_KEY 가 없어 만들 수 없습니다")
    done = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    have = {(d["place"], d["problem"], d["action"], d["result"], d["length"], d["voice_mode"]) for d in done}
    todo = [j for j in jobs if (j[0].place, j[0].problem, j[0].action, j[0].result, j[1], j[2]) not in have]
    print(f"{len(todo)}편 만듭니다 (이미 {len(done)}편)", flush=True)

    def one(job):
        combo, length, mode = job
        try:
            r = make_one(writer, combo, length, mode)
            print(f"  ok {length}/{mode} {combo.key()} {r['seconds']}s {'⚠ ' + '; '.join(r['problems']) if r['problems'] else ''}", flush=True)
            return r
        except Exception as e:  # 한 편이 실패해도 나머지는 만든다
            print(f"  실패 {length}/{mode} {combo.key()}: {e}", flush=True)
            return None

    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(one, todo):
            if r:
                done.append(r)
                out.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out} ({len(done)}편)")


def load(path: Path) -> None:
    """초안을 card_stories 표에 draft 로 넣는다. 같은 조합이 있으면 내용을 바꾸고 draft 로 되돌린다."""

    from app.db.session import SessionLocal
    from app.models.card_story import CardStory

    rows = json.loads(path.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        for r in rows:
            key = {k: r[k] for k in ("place", "problem", "action", "result", "length", "voice_mode")}
            row = db.query(CardStory).filter_by(**key).one_or_none() or CardStory(**key)
            row.payload, row.model, row.prompt_version = r["payload"], r["model"], r["prompt_version"]
            row.status, row.reviewed_by, row.reviewed_at = "draft", None, None
            db.add(row)
        db.commit()
    print(f"{len(rows)}편을 draft 로 넣었습니다 ({datetime.now(UTC):%H:%M})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--load", type=Path)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.load:
        load(a.load)
    elif a.pilot:
        jobs = [(c, length, "narration") for c in PILOT for length in cs.LENGTHS]
        jobs += [(c, "long", "characters") for c in PILOT_CHARACTERS]
        generate(jobs, OUT / "pilot.json", a.workers)
    elif a.all:
        jobs = [(c, length, mode) for c in cs.combos() for length in cs.LENGTHS for mode in cs.VOICE_MODES]
        generate(jobs, OUT / "all.json", a.workers)
    else:
        ap.print_help()
