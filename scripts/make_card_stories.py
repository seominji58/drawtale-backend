"""카드 이야기 미리 만들기 (pt_docs 13 1-1). 사람이 읽기 전 초안을 JSON 으로 남긴다.

    python -m scripts.make_card_stories --pilot              # 시험 8편 (아래 PILOT)
    python -m scripts.make_card_stories --all --workers 6    # 512 조합 × 두 길이 × 두 읽어주기 = 2,048편 — 비용이 크다, 허락받고
    python -m scripts.make_card_stories --load storage/card_stories/pilot.json   # DB 에 draft 로 넣기

만드는 길은 서비스와 같다 — story_writer.StoryWriter (긴 이야기는 Claude, 짧은 이야기는 gpt-4o-mini,
검열 · 카드 뜻 검사 · 다시 쓰기 그대로). 캐릭터 목소리 판은 자리 이름 「라라」로 쓰고 이름 자리로 바꿔 둔다.
결과는 storage/card_stories/ (git 에 올리지 않는 폴더). 키는 .env 에서 읽는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.services import card_stories as cs
from app.services import card_variants
from app.services.book import OPENINGS, book_problems
from app.services.story_writer import get_story_writer

OUT = Path("storage/card_stories")
# 지시문을 고치면 올린다 — 저장본이 어느 지시문으로 만든 것인지 남긴다 (13 7장)
PROMPT_VERSION = "2026-10-28d"  # d: 나머지 판은 기본판을 고쳐 씀 / b: 햇살 첫 문장 · 교훈 끝말 없앰, 조합마다 시작 방식 / c: 첫 쪽 주인공만 · 시간대 · 가려던 곳 · 손 흔들며 끝맺기 금지, 카드 4차

# 시험 8편: 문제마다 2편, 장소 · 행동 · 결과가 겹치지 않게. 안전 수칙 카드(그 자리에서 기다렸어요 · 어른에게 물어봤어요)와
# 「숨었어요」(주어 지키기), 「모두 기뻐했어요」를 넣었다
PILOT = [
    cs.Combo("집", "넘어졌어요", "툭툭 털고 달려갔어요", "가려던 곳까지 갔어요"),
    cs.Combo("우주", "넘어졌어요", "어른을 불렀어요", "친구가 생겼어요"),
    cs.Combo("놀이터", "친구가 울고 있었어요", "다가가서 도와줬어요", "친구 눈물이 그쳤어요"),
    cs.Combo("바다", "친구가 울고 있었어요", "옆에서 기다려 줬어요", "고맙다는 말을 들었어요"),
    cs.Combo("숲", "길을 잃었어요", "그 자리에서 기다렸어요", "길을 찾았어요"),
    cs.Combo("마을", "길을 잃었어요", "표지판으로 달려갔어요", "어른이 데려다 줬어요"),
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
    opening = ""
    if length == "long":
        # 조합마다 정해진 시작 방식 — 다시 만들어도 같은 조합은 같은 방식이다
        opening = OPENINGS[cs.combos().index(combo) % len(OPENINGS)]
        book = writer.write_book(choices, set(), s.story_pages, characters, name, opening=opening)
        problems = book_problems(book, choices, s.story_pages, characters)
        payload = cs.book_payload(book)
    elif characters:
        script = writer.write_script(combo.place, combo.problem, combo.action, combo.result, name)
        payload = cs.short_payload(sentences=[[(ln.speaker, ln.text, ln.emotion) for ln in parts] for parts in script.sentences], cast=script.cast)
    else:
        payload = cs.short_payload(text=writer.write(combo.place, combo.problem, combo.action, combo.result))
    # 실제로 답한 모델 (마지막 시도). 미리 만드는 이야기는 전부 Claude 로 — 예비 모델로 넘어간 편은 버리고 다시 만든다
    model = writer.last_model
    if not model.startswith("claude"):
        raise RuntimeError(f"Claude 가 아니라 {model or '틀 문장'} 으로 만들어졌다 — 버린다")
    return {
        "place": combo.place, "problem": combo.problem, "action": combo.action, "result": combo.result,
        "length": length, "voice_mode": mode, "payload": payload, "model": model,
        "prompt_version": PROMPT_VERSION, "problems": problems, "opening": opening if length == "long" else "",
        "usage": dict(writer.usage), "seconds": round(time.time() - t, 1),
    }


def read_rows(out: Path) -> list[dict]:
    """.json(목록) 이나 .jsonl(한 줄에 한 편) 모두 읽는다."""

    if not out.exists():
        return []
    text = out.read_text(encoding="utf-8")
    if out.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def generate(jobs: list[tuple[cs.Combo, str, str]], out: Path, workers: int) -> None:
    """조합마다: 기본판(긴 판 · 나레이션)을 쓰고, 나머지 판은 기본판을 고쳐 쓴다 (card_variants).

    jobs 에 든 (조합, 길이, 읽어주기)만 만든다. 이미 있는 것은 건너뛰고, 기본판이 이미 있으면 다시 쓰지 않는다.
    """

    if get_story_writer() is None:
        sys.exit("OPENAI_API_KEY 가 없어 만들 수 없습니다")
    done = read_rows(out)
    have = {(d["place"], d["problem"], d["action"], d["result"], d["length"], d["voice_mode"]): d for d in done}
    by_combo: dict[cs.Combo, list[tuple[str, str]]] = {}
    for combo, length, mode in jobs:
        if (combo.place, combo.problem, combo.action, combo.result, length, mode) not in have:
            by_combo.setdefault(combo, []).append((length, mode))
    total = sum(len(v) for v in by_combo.values())
    print(f"{total}편 만듭니다 (조합 {len(by_combo)}개, 이미 {len(done)}편)", flush=True)
    lock = threading.Lock()
    tally = {"ok": 0, "fail": 0, "in": 0, "out": 0}

    def save(r: dict) -> None:
        with lock:
            if out.suffix == ".jsonl":
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            else:
                done.append(r)
                out.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
            tally["ok"] += 1
            tally["in"] += r["usage"]["input"]
            tally["out"] += r["usage"]["output"]
            n = tally["ok"] + tally["fail"]
        warn = "⚠ " + "; ".join(r["problems"]) if r["problems"] else ""
        print(f"  ok {n}/{total} {r['length']}/{r['voice_mode']} {r['place']} / {r['problem']} / {r['action']} / {r['result']} {r['seconds']}s {warn}", flush=True)
        if n % 50 == 0:
            print(f"  … {n}/{total} · 실패 {tally['fail']} · 토큰 입력 {tally['in']:,} 출력 {tally['out']:,}", flush=True)

    def fail(combo, length, mode, e):
        with lock:
            tally["fail"] += 1
        print(f"  실패 {length}/{mode} {combo.key()}: {e}", flush=True)

    def one(combo: cs.Combo):
        want = by_combo[combo]
        key = (combo.place, combo.problem, combo.action, combo.result)
        base = have.get(key + ("long", "narration"))
        if ("long", "narration") in want:
            try:
                base = make_one(get_story_writer(), combo, "long", "narration")
                save(base)
            except Exception as e:
                fail(combo, "long", "narration", e)
                return
        for length, mode in want:
            if (length, mode) == ("long", "narration"):
                continue
            if base is None:
                fail(combo, length, mode, "기본판이 없다")
                continue
            writer = get_story_writer()  # 작업마다 따로 — last_model · usage 가 섞이지 않게
            t = time.time()
            try:
                payload, problems = card_variants.VARIANTS[(length, mode)](writer, combo, base["payload"], get_settings().story_pages)
            except Exception as e:
                fail(combo, length, mode, e)
                continue
            save({
                "place": combo.place, "problem": combo.problem, "action": combo.action, "result": combo.result,
                "length": length, "voice_mode": mode, "payload": payload, "model": writer.last_model,
                "prompt_version": PROMPT_VERSION, "problems": problems, "opening": "",
                "derived_from": "long-narration", "usage": dict(writer.usage), "seconds": round(time.time() - t, 1),
            })

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, list(by_combo)))
    print(f"저장: {out} · 새로 {tally['ok']}편 · 실패 {tally['fail']}편 · 토큰 입력 {tally['in']:,} 출력 {tally['out']:,}")


def load(path: Path) -> None:
    """초안을 card_stories 표에 draft 로 넣는다. 같은 조합이 있으면 내용을 바꾸고 draft 로 되돌린다."""

    from app.db.session import SessionLocal
    from app.models.card_story import CardStory

    rows = read_rows(path)
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
        generate(jobs, OUT / "all.jsonl", a.workers)
    else:
        ap.print_help()
