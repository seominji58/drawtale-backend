"""미리 만든 카드 이야기 자동 1차 검토 (Claude Opus 5.5, 배치 반값) — 사람이 읽을 편을 고른다 (2026-10-28).

    python -m scripts.review_card_stories --budget 9

조합 하나(장소 · 문제 · 행동 · 결과)마다 네 판(긴/짧은 × 나레이션/캐릭터)을 한 번에 보여 주고
pt_docs 13 의 규칙으로 판정하게 한다. 결과 storage/card_stories/review_ai.jsonl — 검토 페이지가 「AI 가 짚은 것」으로 보여 준다.
판정은 사람 검토를 대신하지 않는다. **짚은 편은 반드시 사람이 읽고, 짚지 않은 편은 표본만 읽는다.**
꺼졌다 켜도 보낸 배치를 이어 받는다 (review_state.json).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

from app.core.config import get_settings
from app.services.story_writer import json_part
from scripts.batch_card_stories import COMBO_BY_KEY, PRICE_IN, PRICE_OUT, load_rows, log, sig

OUT = Path("storage/card_stories")
RESULTS = OUT / "review_ai.jsonl"
STATE = OUT / "review_state.json"
RUBRIC_VERSION = "2026-10-28"
AVG_IN, AVG_OUT = 3200, 900  # 조합 하나 (네 판) 추정 — 실제 usage 로 다시 센다

SYSTEM = """너는 아이(만 4~7세)에게 들려줄 그림책 이야기를 검토하는 편집자야. 아이가 카드 네 장(장소 · 문제 · 행동 · 결과)을 골랐고,
이야기 하나를 네 판(긴 판 · 나레이션 / 긴 판 · 캐릭터 목소리 / 짧은 판 · 나레이션 / 짧은 판 · 캐릭터 목소리)으로 미리 만들어 두었다.
사람이 다 읽기 전에, **사람이 꼭 읽어야 할 문제**만 짚어 줘. 사소한 취향은 짚지 않는다.

[짚을 문제]
1. 카드 뜻 — 카드의 뜻을 바꾸거나 뒤집음. 주어가 바뀜(「도와줬어요」는 주인공이 도운 것, 「숨었어요」는 주인공이 숨은 것).
   카드 글자가 그 단계에 없음.
2. 고르지 않은 것 — 카드에 없는 큰 사건 · 새 문제 · 악당 · 다른 결말, 고르지 않은 가족 · 친구가 처음부터 함께 옴,
   다른 장소로 옮겨 감(카드가 옮겨 가는 뜻인 「어른이 데려다 줬어요」 · 「무사히 돌아왔어요」는 괜찮다. 「가려던 곳」은 같은 장소 안이어야 한다).
3. 안전 — 따라 하면 위험한 행동(높은 곳에서 뛰어내리기 · 불 · 찻길 · 약 · 모르는 사람 따라가기), 누구인지 모르는 어른을 따라감,
   다치는 모습을 자세히 그림, 무섭거나 슬프게 끝남, 아이에게 상처가 될 말.
4. 이름 자리 — 캐릭터 판의 {이름}{조사:X} 자리가 이상함(조사 자리 아닌 곳, 이름이 들어가면 어색한 곳, 주인공이 「나」로 남음).
5. 판 사이 불일치 — 네 판의 줄거리 · 등장인물 · 결말이 서로 다름(짧은 판은 줄인 것이라 빠지는 건 괜찮다. 다른 내용이 생기면 문제).
6. 어색한 문장 — 아이가 이해하기 어려운 말 · 어색한 문장 · 앞뒤가 안 맞음(아이 카드가 엉뚱해서 생긴 신기함은 문제 아님 — 우주에서 넘어져도 된다).

[답]
JSON 하나로만 답한다.
{"verdict": "pass" 또는 "check",
 "issues": [{"version": "long-narration|long-characters|short-narration|short-characters|all",
             "type": "카드 뜻|고르지 않은 것|안전|이름 자리|판 사이 불일치|어색한 문장",
             "quote": "문제 되는 글을 그대로 짧게",
             "why": "한 문장"}]}
문제가 없으면 {"verdict": "pass", "issues": []}. 하나라도 있으면 "check"."""


def versions_text(k: str, rows: dict) -> str:
    out = []
    for length, mode, name in (
        ("long", "narration", "긴 판 · 나레이션 (기본판)"),
        ("long", "characters", "긴 판 · 캐릭터 목소리"),
        ("short", "narration", "짧은 판 · 나레이션"),
        ("short", "characters", "짧은 판 · 캐릭터 목소리"),
    ):
        r = rows.get((k, length, mode))
        if r is None:
            out.append(f"## {name}\n(없음)")
            continue
        p = r["payload"]
        if "book" in p:
            lines = [f"제목: {p['book']['title']}"]
            for pg in p["book"]["pages"]:
                said = " ".join(
                    ln["text"] if ln["speaker"] == "나레이션" else f"[{ln['speaker']}] “{ln['text']}”" for ln in pg["lines"]
                )
                lines.append(f"{pg['page']}쪽 ({pg['stage']}): {said}")
            body = "\n".join(lines)
        elif "sentences" in p:
            body = " ".join(t if s == "나레이션" else f"[{s}] “{t}”" for parts in p["sentences"] for s, t, e in parts)
        else:
            body = p["text"]
        out.append(f"## {name}\n{body}")
    return "\n\n".join(out)


def request(k: str, rows: dict) -> dict:
    c = COMBO_BY_KEY[k]
    user = f"카드: 장소 「{c.place}」 · 문제 「{c.problem}」 · 행동 「{c.action}」 · 결과 「{c.result}」\n\n{versions_text(k, rows)}"
    return {
        "model": get_settings().story_book_claude_model, "max_tokens": 6000,
        "system": SYSTEM, "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": "low"},
    }


def fingerprint(k: str, rows: dict) -> str:
    """네 판 내용의 지문 — 이야기를 다시 만들면 다시 검토한다."""
    return sig({f"{l}-{m}": (rows.get((k, l, m)) or {}).get("payload") for l in ("long", "short") for m in ("narration", "characters")})


def load_results() -> dict:
    res = {}
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                res[r["combo"]] = r
    return res


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def wait(client, bid):
    while True:
        b = client.messages.batches.retrieve(bid)
        if b.processing_status == "ended":
            return list(client.messages.batches.results(bid))
        rc = b.request_counts
        log(f"  {bid} 처리 중 · 끝남 {rc.succeeded + rc.errored}")
        time.sleep(60)


def process(results, rows, state) -> float:
    spent = 0.0
    done = load_results()
    with RESULTS.open("a", encoding="utf-8") as f:
        for res in results:
            k = res.custom_id.split("-", 1)[1]
            if res.result.type != "succeeded":
                state["failed"][k] = state["failed"].get(k, 0) + 1
                continue
            msg = res.result.message
            u = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
            spent += u["input"] * PRICE_IN + u["output"] * PRICE_OUT
            fp = fingerprint(k, rows)
            if done.get(k, {}).get("fingerprint") == fp:
                continue
            text = "".join(b.text for b in msg.content if b.type == "text")
            try:
                got = json.loads(json_part(text))
                verdict = "check" if got.get("issues") else str(got.get("verdict") or "pass")
                issues = [i for i in got.get("issues") or [] if isinstance(i, dict)]
            except (ValueError, json.JSONDecodeError):
                state["failed"][k] = state["failed"].get(k, 0) + 1
                continue
            c = COMBO_BY_KEY[k]
            r = {"combo": k, "place": c.place, "problem": c.problem, "action": c.action, "result": c.result,
                 "verdict": verdict, "issues": issues, "fingerprint": fp, "rubric": RUBRIC_VERSION, "usage": u}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            done[k] = r
    return spent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    s = get_settings()
    if not s.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY 가 없다")
    client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=120)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state.setdefault("failed", {})
    state.setdefault("batches", [])
    state.setdefault("processed", [])
    rows = load_rows()
    for bid in [b for b in state["batches"] if b not in state["processed"]]:
        log(f"전에 보낸 검토 배치 {bid} 를 이어 받는다")
        cost = process(wait(client, bid), rows, state)
        state["processed"].append(bid)
        state["spent"] = state.get("spent", 0) + cost
        save_state(state)
    spent = 0.0
    for round_no in range(1, 4):
        done = load_results()
        todo = [k for k in COMBO_BY_KEY if done.get(k, {}).get("fingerprint") != fingerprint(k, rows) and state["failed"].get(k, 0) < 3]
        if a.limit:
            todo = todo[: a.limit]
        if not todo:
            log("검토할 것이 없다 — 끝")
            break
        each = AVG_IN * PRICE_IN + AVG_OUT * PRICE_OUT
        fit = int(max(0.0, a.budget - spent) // each)
        if fit == 0:
            log(f"멈춤: 예산 ${a.budget:.2f} 중 ${spent:.2f} 를 썼다")
            break
        todo = todo[:fit]
        log(f"{round_no}회차 · 조합 {len(todo)}개 · 예상 ${len(todo) * each:.2f}")
        batch = client.messages.batches.create(requests=[{"custom_id": f"rv-{k}", "params": request(k, rows)} for k in todo])
        state["batches"].append(batch.id)
        save_state(state)
        cost = process(wait(client, batch.id), rows, state)
        spent += cost
        state["processed"].append(batch.id)
        state["spent"] = state.get("spent", 0) + cost
        save_state(state)
        res = load_results()
        log(f"{round_no}회차 끝 · ${cost:.2f} · 지금까지 판정 {len(res)} · 짚은 조합 {sum(r['verdict'] == 'check' for r in res.values())}")
        if a.limit:
            break
    log(f"이번 실행 ${spent:.2f}")


if __name__ == "__main__":
    main()
