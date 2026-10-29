"""카드 이야기 미리 만들기 — 배치 API 판 (반값, 2026-10-28). 결과는 make_card_stories 와 같은 all.jsonl 에 이어 붙인다.

    python -m scripts.batch_card_stories --budget 55          # 남은 것 전부 (예산을 넘기 전에 멈춘다)
    python -m scripts.batch_card_stories --budget 2 --limit 4 # 시험

한 번 돌리면 다음을 차례로 한다. 중간에 끊겨도 다시 돌리면 남은 것만 한다.
  1단계  기본판(긴 판 · 나레이션)
         · 없으면 새로 쓴다 (지시문 판 PROMPT_VERSION)
         · 예전 지시문으로 쓴 것은 첫 쪽 · 제목 · 첫 쪽 배경만 다시 쓴다 — 같은 장소에서 첫 쪽이 한 자리로 몰렸다
           (집 63편 중 52편이 「우리 집 거실에…」). 누구인지 모를 어른이 나오는 것은 통째로 새로 쓴다
  2단계  나머지 세 판 — 기본판을 고쳐 쓴다 (card_variants). 기본판이 바뀌었으면 다시 고쳐 쓴다
  실패 · 검사에 걸린 것은 같은 단계를 두 번까지 다시 보낸다.
비용: Opus 5.5 배치 단가 입력 $2 · 출력 $10 / 100만 토큰 (skill 문서 2026-10). 실제 usage 로 센다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import anthropic

from app.core.config import get_settings
from app.services import card_stories as cs
from app.services import card_variants as cv
from app.services.book import OPENINGS, book_problems, book_prompt, book_user_message, mentions, parse_book
from app.services.story_writer import StoryBlocked, get_story_writer, json_part
from app.services.voices import NARRATOR

OUT = Path("storage/card_stories")
ROWS = OUT / "all.jsonl"
STATE = OUT / "batch_state.json"
PROMPT_VERSION = "2026-10-28f"  # f: 「어두워졌어요」에서 혼자 밤에 남지 않기 / e: 첫 쪽 자리 돌리기 · 제목 · 이름 밝힌 어른, 배치
FIX_BEFORE = "2026-10-28e"  # 이보다 앞선 기본판만 첫 쪽을 고친다 (e 는 이미 첫 쪽 자리를 돌려 받았다)
# f 에서 다시 쓰는 기본판 — AI 검토가 「혼자 · 밤」을 짚은 문제 (46조합 중 39). 같은 문제 128조합을 모두 다시 쓴다
REWRITE_PROBLEMS = {"어두워졌어요"}
REWRITE_BEFORE = "2026-10-28f"  # 이보다 앞선 「어두워졌어요」 기본판만 다시 쓴다
# AI 검토가 짚은 것을 고쳐 쓰는 회차 (scripts/fix_card_stories.py). 고친 줄에 fixed_round 로 남는다
FIX_ROUND = "2026-10-29"
FIX_CODES = {"fixbase": ("long", "narration"), "fixlc": ("long", "characters"), "fixsn": ("short", "narration"), "fixsc": ("short", "characters")}
PRICE_IN, PRICE_OUT = 2 / 1e6, 10 / 1e6
# 한 요청 평균 토큰 (2026-10-28 실측) — 보내기 전에 예산을 넘는지 본다
AVG = {"base": (5200, 5600), "fix": (3500, 1200), "lc": (1800, 1650), "sn": (1800, 700), "sc": (2600, 1350)}
KIND_CODE = {("long", "characters"): "lc", ("short", "narration"): "sn", ("short", "characters"): "sc"}
CODE_KIND = {v: k for k, v in KIND_CODE.items()}
UNKNOWN_ADULT = re.compile(r"아저씨|아주머니")
KNOWN_ROLE = re.compile(r"경찰|지킴이|선생님|주인|가게|안내|직원|돌보는|관리|엄마|아빠|할머니|할아버지")

FIX_SYSTEM = """너는 그림책 대본의 **첫 쪽과 제목만** 다시 쓰는 도우미야. 나머지 쪽은 그대로 둔다.
- 첫 쪽은 「{place}」의 「{spot}」에서 시작한다. 그 자리에서만 보이고 들리는 것으로 장면을 연다.
- 첫 쪽에 장소 이름 「{place}」를 그대로 한 번 쓴다. 주인공은 「나」. 주인공 말고 아무도 나오지 않는다.
- 햇살 · 날씨로 시작하지 않는다. 문제가 「어두워졌어요」면 밤 · 저녁으로 시작하지 않는다.
- 문장 2~4개, 문장 하나 15~35자, 초등학교 1학년이 아는 낱말, 「~어요」. 흉내 내는 말 하나.
- 2쪽과 자연스럽게 이어지게 쓴다. 2쪽 이후에 나오는 것을 1쪽에서 미리 말하지 않는다.
- 제목은 8~15자, 이 이야기에만 나오는 물건이나 자리를 넣는다. 카드 글자를 그대로 제목으로 쓰지 않는다.
- background 는 새 첫 쪽의 배경 그림 지시문이다. 영어 1~2문장, 장소 · 시간 · 보이는 것.
  주인공 이름 · 글자 · 간판을 넣지 않는다. 화풍은 쓰지 않는다.
JSON 으로만 답한다: {{"title": "…", "lines": [{{"speaker": "{narrator}", "text": "…"}}], "background": "…"}}"""


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def ckey(c: cs.Combo) -> str:
    return "%d%d%d%d" % (
        cs.PLACES.index(c.place) + 1, cs.PROBLEMS.index(c.problem) + 1,
        cs.ACTIONS[c.problem].index(c.action) + 1, cs.RESULTS[c.problem].index(c.result) + 1,
    )


COMBO_BY_KEY = {ckey(c): c for c in cs.combos()}


def sig(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def load_rows() -> dict[tuple, dict]:
    """all.jsonl → (조합 키, 길이, 읽어주기) → 마지막 줄 (뒤에 쓴 것이 이긴다)."""

    rows = {}
    if ROWS.exists():
        for line in ROWS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                c = cs.Combo(r["place"], r["problem"], r["action"], r["result"])
                rows[(ckey(c), r["length"], r["voice_mode"])] = r
    return rows


def append(r: dict) -> None:
    with ROWS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


def base_text(payload: dict) -> str:
    return " ".join(p["text"] for p in payload["book"]["pages"])


def row(c: cs.Combo, length: str, mode: str, payload: dict, problems: list[str], usage: dict, **extra) -> dict:
    return {
        "place": c.place, "problem": c.problem, "action": c.action, "result": c.result,
        "length": length, "voice_mode": mode, "payload": payload, "model": get_settings().story_book_claude_model,
        "prompt_version": PROMPT_VERSION, "problems": problems, "usage": usage, "seconds": 0, "batch": True, **extra,
    }


# ── 요청 ──
def base_request(c: cs.Combo) -> dict:
    s = get_settings()
    choices = {"place": c.place, "problem": c.problem, "action": c.action, "result": c.result}
    opening = OPENINGS[cs.combos().index(c) % len(OPENINGS)]
    spot = cs.spot_for(c)
    user = book_user_message(choices, set(), None, opening) + (
        f"\n첫 쪽 자리 (꼭 지킨다): {c.place}의 「{spot}」. 첫 쪽은 「{spot}」에 있는 주인공으로 시작하고 「{spot}」라는 말을 쓴다."
        f" 다른 자리(예: {'거실' if spot != '거실' else '부엌'})에서 시작하지 않는다."
    )
    return {
        "model": s.story_book_claude_model, "max_tokens": 16000,
        "system": book_prompt(s.story_pages, False, None),
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": s.story_book_effort},
    }


def fix_request(c: cs.Combo, base: dict) -> dict:
    s = get_settings()
    pages = "\n".join(f"{p['page']}쪽: {p['text']}" for p in base["book"]["pages"])
    user = (f"카드: 장소 {c.place} · 문제 {c.problem} · 행동 {c.action} · 결과 {c.result}\n"
            f"지금 제목: {base['book']['title']}\n지금 대본\n{pages}")
    return {
        "model": s.story_book_claude_model, "max_tokens": 4000,
        "system": FIX_SYSTEM.format(place=c.place, spot=cs.spot_for(c), narrator=NARRATOR),
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": "low"},
    }


def variant_request(kind: tuple[str, str], c: cs.Combo, base: dict) -> dict:
    s = get_settings()
    system, user, _ = cv.request(kind, c, base)
    return {
        "model": s.story_short_claude_model, "max_tokens": 8000, "system": system,
        "messages": [{"role": "user", "content": user}], "output_config": {"effort": "low"},
    }


# ── 결과 읽기 ──
def spot_missing(c: cs.Combo, first_page: str) -> bool:
    """첫 쪽에 정해 준 자리가 없나. 「욕실 앞」 → 「욕실」, 「버섯이 난 풀밭」 → 「풀밭」 중 하나라도 있으면 된다."""

    # 「내 방」 · 「별 사이」 · 「달 위」처럼 한 글자 낱말도 본다 (두 글자만 보다가 늘 「없다」로 판정했다, 2026-10-28)
    fillers = {"앞", "옆", "아래", "위", "사이", "곳", "내", "난", "이", "가"}
    words = [w.rstrip("이가") or w for w in cs.spot_for(c).split() if w not in fillers]
    return not any(w and w in first_page for w in words)


def moderate(writer, text: str) -> list[str]:
    try:
        writer.check_input(text)
        return []
    except StoryBlocked as e:
        return [f"내용 검사에 걸림 ({e})"]


def read_base(c: cs.Combo, text: str, writer) -> tuple[dict, list[str]]:
    s = get_settings()
    book = parse_book(json_part(text), None)
    for p in book.pages:
        p.child_words = ""
    choices = {"place": c.place, "problem": c.problem, "action": c.action, "result": c.result}
    problems = book_problems(book, choices, s.story_pages, False)
    if spot_missing(c, book.pages[0].text()):
        problems.append(f"첫 쪽에 자리 「{cs.spot_for(c)}」가 없다")
    if UNKNOWN_ADULT.search(book.text()) and not KNOWN_ROLE.search(book.text()):
        problems.append("누구인지 모를 어른")
    return cs.book_payload(book), problems + moderate(writer, book.text())


def read_fix(c: cs.Combo, text: str, base: dict, writer) -> tuple[dict, list[str]]:
    got = json.loads(json_part(text))
    lines = [{"speaker": NARRATOR, "text": str(ln["text"]).strip(), "emotion": ""} for ln in got["lines"] if str(ln.get("text") or "").strip()]
    if not lines:
        raise ValueError("첫 쪽이 비었다")
    payload = json.loads(json.dumps(base))
    p1 = payload["book"]["pages"][0]
    p1["lines"] = lines
    p1["text"] = " ".join(ln["text"] for ln in lines)
    if p1.get("scene") and str(got.get("background") or "").strip():
        p1["scene"]["background"] = str(got["background"]).strip()
    payload["book"]["title"] = str(got.get("title") or payload["book"]["title"]).strip()
    book = cs.book_from_payload(payload, None)
    choices = {"place": c.place, "problem": c.problem, "action": c.action, "result": c.result}
    problems = book_problems(book, choices, get_settings().story_pages, False)
    if not mentions(c.place, p1["text"]):
        problems.append("첫 쪽에 장소 이름이 없다")
    if spot_missing(c, p1["text"]):
        problems.append(f"첫 쪽에 자리 「{cs.spot_for(c)}」가 없다")
    return payload, problems + moderate(writer, p1["text"] + " " + payload["book"]["title"])


# ── 할 일 고르기 ──
def needs_full(base_row: dict) -> bool:
    t = base_text(base_row["payload"])
    return bool(UNKNOWN_ADULT.search(t) and not KNOWN_ROLE.search(t))


REDO_FLAGGED = False  # --redo-flagged: 세 번 걸린 채 저장된 편을 한 번 더 만든다


def flagged(r: dict | None) -> bool:
    return bool(REDO_FLAGGED and r and r.get("problems"))


def plan(rows: dict, failed: dict) -> tuple[dict, dict]:
    """(1단계 요청, 2단계 요청). 키는 custom_id. 세 번 걸린 것은 걸린 채로 저장돼 있어 다시 보내지 않는다."""

    stage1, stage2 = {}, {}
    for k, c in COMBO_BY_KEY.items():
        b = rows.get((k, "long", "narration"))
        if b is None or flagged(b):
            if failed.get(f"base-{k}", 0) < 3:
                stage1[f"base-{k}"] = base_request(c)
            continue
        if b["prompt_version"] < REWRITE_BEFORE and c.problem in REWRITE_PROBLEMS:
            if failed.get(f"base-{k}", 0) < 3:
                stage1[f"base-{k}"] = base_request(c)
            continue
        if b["prompt_version"] < FIX_BEFORE:
            code = "base" if needs_full(b) else "fix"
            if failed.get(f"{code}-{k}", 0) < 3:
                stage1[f"{code}-{k}"] = base_request(c) if code == "base" else fix_request(c, b["payload"])
            continue
        bsig = sig(b["payload"])
        for kind, code in KIND_CODE.items():
            d = rows.get((k, *kind))
            if (d is None or d.get("base_sig") != bsig or flagged(d)) and failed.get(f"{code}-{k}", 0) < 3:
                stage2[f"{code}-{k}"] = variant_request(kind, c, b["payload"])
    return stage1, stage2


def estimate(ids) -> float:
    return sum(AVG[i.split("-")[0]][0] * PRICE_IN + AVG[i.split("-")[0]][1] * PRICE_OUT for i in ids)


# ── 배치 ──
def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def submit(client, reqs: dict, state: dict) -> str:
    batch = client.messages.batches.create(requests=[{"custom_id": k, "params": v} for k, v in reqs.items()])
    state.setdefault("batches", []).append(batch.id)
    save_state(state)
    log(f"배치 {batch.id} 보냄 · {len(reqs)}건")
    return batch.id


def wait(client, batch_id: str) -> list:
    """배치가 끝날 때까지 기다렸다가 결과를 받는다. 노트북이 꺼져도 배치는 서버에서 돈다 — 다시 켜면 여기서 이어 받는다."""

    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        rc = b.request_counts
        log(f"  {batch_id} 처리 중 · 끝남 {rc.succeeded + rc.errored} / {rc.processing + rc.succeeded + rc.errored + rc.canceled + rc.expired}")
        time.sleep(60)
    return list(client.messages.batches.results(batch_id))


def already_saved(rows: dict, code: str, k: str) -> bool:
    """이 요청의 결과가 이미 저장됐나 — 같은 배치를 두 번 읽어도 두 번 저장하지 않는다."""

    b = rows.get((k, "long", "narration"))
    if code in FIX_CODES:
        d = rows.get((k, *FIX_CODES[code]))
        if d is None or d.get("fixed_round") != FIX_ROUND:
            return False
        return code == "fixbase" or (b is not None and d.get("base_sig") == sig(b["payload"]))
    if code == "base" and flagged(b):
        return False
    if code in KIND_CODE.values() and flagged(rows.get((k, *CODE_KIND[code]))):
        return False
    if code == "base":
        return b is not None and b["prompt_version"] == PROMPT_VERSION
    if code == "fix":
        return b is not None and b["prompt_version"] >= FIX_BEFORE
    d = rows.get((k, *CODE_KIND[code]))
    return b is not None and d is not None and d.get("base_sig") == sig(b["payload"])


def process(results: list, state: dict, writer) -> tuple[int, int, float]:
    """배치 결과를 검사하고 저장한다. (저장, 다시/실패, 쓴 돈)."""

    s = get_settings()
    rows = load_rows()
    ok = bad = 0
    spent = 0.0
    for res in results:
        cid = res.custom_id
        code, k = cid.split("-", 1)
        c = COMBO_BY_KEY[k]
        if res.result.type != "succeeded":
            state["failed"][cid] = state["failed"].get(cid, 0) + 1
            bad += 1
            continue
        msg = res.result.message
        u = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
        spent += u["input"] * PRICE_IN + u["output"] * PRICE_OUT
        if already_saved(rows, code, k):
            continue
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            if code == "base":
                payload, problems = read_base(c, text, writer)
                r = row(c, "long", "narration", payload, problems, u, opening=OPENINGS[cs.combos().index(c) % len(OPENINGS)], spot=cs.spot_for(c))
            elif code == "fixbase":
                payload, problems = read_base(c, text, writer)
                r = row(c, "long", "narration", payload, problems, u, fixed_round=FIX_ROUND, spot=cs.spot_for(c))
            elif code in FIX_CODES:
                kind = FIX_CODES[code]
                base = rows[(k, "long", "narration")]["payload"]
                payload, problems, check = cv.parse(kind, json_part(text) if kind != ("short", "narration") else text, c, base, s.story_pages)
                problems = problems + moderate(writer, check)
                r = row(c, *kind, payload, problems, u, derived_from="long-narration", base_sig=sig(base), fixed_round=FIX_ROUND)
            elif code == "fix":
                old = rows[(k, "long", "narration")]["payload"]
                payload, problems = read_fix(c, text, old, writer)
                r = row(c, "long", "narration", payload, problems, u, fixed=True, spot=cs.spot_for(c))
            else:
                kind = CODE_KIND[code]
                base = rows[(k, "long", "narration")]["payload"]
                payload, problems, check = cv.parse(kind, json_part(text) if kind != ("short", "narration") else text, c, base, s.story_pages)
                problems = problems + moderate(writer, check)
                r = row(c, *kind, payload, problems, u, derived_from="long-narration", base_sig=sig(base))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            state["failed"][cid] = state["failed"].get(cid, 0) + 1
            bad += 1
            log(f"  형식 오류 {cid}: {e}")
            continue
        if r["problems"]:
            state["failed"][cid] = state["failed"].get(cid, 0) + 1
            bad += 1
            log(f"  검사 걸림 {cid}: {'; '.join(r['problems'])}")
            if state["failed"][cid] < 3:
                continue  # 다음 회차에 다시 보낸다
        append(r)
        rows[(k, r["length"], r["voice_mode"])] = r
        ok += 1
    return ok, bad, spent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True, help="이번 실행에서 새로 보낼 요청에 쓸 최대 달러")
    ap.add_argument("--limit", type=int, default=0, help="시험: 단계마다 이 수만 보낸다")
    ap.add_argument("--only", nargs="*", default=[], help="시험: 이 종류만 (base fix lc sn sc)")
    ap.add_argument("--redo-flagged", action="store_true", help="검사에 걸린 채 저장된 편을 한 번 더 만든다")
    a = ap.parse_args()
    global REDO_FLAGGED
    REDO_FLAGGED = a.redo_flagged
    s = get_settings()
    if not s.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY 가 없다")
    client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=120)
    writer = get_story_writer()  # 검열(OpenAI moderation)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state.setdefault("failed", {})
    state.setdefault("processed", [])
    spent = 0.0

    # 고친 자리 검사로 다시 판정: 저장된 기본판 중 「자리가 없다」로만 걸렸던 것은 지금 기준으로 다시 본다
    if not state.get("recheck_1028"):
        fixed_rows = 0
        for (k, length, mode), r in load_rows().items():
            if (length, mode) != ("long", "narration") or not r.get("problems"):
                continue
            rest = [p for p in r["problems"] if "자리" not in p]
            if rest != r["problems"] and not spot_missing(COMBO_BY_KEY[k], r["payload"]["book"]["pages"][0]["text"]):
                append({**r, "problems": rest})
                fixed_rows += 1
        for cid in list(state["failed"]):
            state["failed"].pop(cid)  # 자리 · 형식 오류로 쌓인 횟수를 지우고 다시 기회를 준다
        state["recheck_1028"] = True
        save_state(state)
        log(f"고친 자리 검사로 {fixed_rows}편을 다시 판정했다 · 실패 횟수 초기화")

    if REDO_FLAGGED:
        for (k, length, mode), r in load_rows().items():
            if r.get("problems"):
                code = "base" if (length, mode) == ("long", "narration") else KIND_CODE[(length, mode)]
                state["failed"].pop(f"{code}-{k}", None)
        save_state(state)

    # 먼저: 보내 놓고 아직 읽지 않은 배치 (노트북이 꺼졌던 사이 서버에서 끝났을 수 있다). 새로 보내지 않고 받아 온다
    for bid in [b for b in state.get("batches", []) if b not in state["processed"]]:
        log(f"전에 보낸 배치 {bid} 를 이어 받는다")
        ok, bad, cost = process(wait(client, bid), state, writer)
        state["processed"].append(bid)
        state["spent"] = state.get("spent", 0) + cost
        save_state(state)
        log(f"  저장 {ok} · 다시/실패 {bad} · ${cost:.2f} (이 배치는 이번 예산에 넣지 않는다 — 이미 낸 돈)")

    for round_no in range(1, 8):
        rows = load_rows()
        stage1, stage2 = plan(rows, state["failed"])
        reqs = stage1 or stage2
        if not reqs:
            log("할 일이 없다 — 끝")
            break
        if a.only:
            reqs = {k: v for k, v in reqs.items() if k.split("-")[0] in a.only}
        if a.limit:
            # 종류마다 limit 개씩
            picked, seen = {}, {}
            for k, v in reqs.items():
                t = k.split("-")[0]
                if seen.get(t, 0) < a.limit:
                    picked[k] = v
                    seen[t] = seen.get(t, 0) + 1
            reqs = picked
        # 남은 예산에 들어가는 만큼만 보낸다 (조합 순서대로 — 조합이 한꺼번에 끝나게)
        room = a.budget - spent
        fit, est = {}, 0.0
        for k, v in reqs.items():
            e = estimate([k])
            if est + e > room:
                break
            fit[k], est = v, est + e
        if not fit:
            log(f"멈춤: 예산 ${a.budget:.2f} 중 ${spent:.2f} 를 썼고, 다음 한 건도 들어가지 않는다")
            break
        if len(fit) < len(reqs):
            log(f"예산 때문에 {len(reqs)}건 중 {len(fit)}건만 보낸다")
        reqs = fit
        log(f"{round_no}회차 · {'1단계(기본판)' if stage1 else '2단계(나머지 판)'} · {len(reqs)}건 · 예상 ${est:.2f}")
        bid = submit(client, reqs, state)
        ok, bad, cost = process(wait(client, bid), state, writer)
        spent += cost
        state["processed"].append(bid)
        state["spent"] = state.get("spent", 0) + cost
        save_state(state)
        log(f"{round_no}회차 끝 · 저장 {ok} · 다시/실패 {bad} · 이번 실행 누적 ${spent:.2f} · 지금까지 전체 ${state['spent']:.2f}")
        if a.limit:
            break
    log(f"이번 실행에서 쓴 돈 약 ${spent:.2f} (배치 단가)")


if __name__ == "__main__":
    main()
