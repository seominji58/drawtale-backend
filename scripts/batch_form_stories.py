"""형태별 카드 이야기 미리 만들기 — 4족형 · 사슬형 … (2026-11-09 사용자 「비휴머노이드는 대본 쓰는 시간이 길다」).

사람형 카드 이야기(batch_card_stories)와 같은 배치 API(반값) · 같은 검사를 쓰고, 지시문에 그 형태의 몸 규칙(hero_form)을 넣는다.
비휴머노이드 이야기는 지금 긴 판 · 나레이션만 쓰므로 기본판 하나만 만든다.
결과는 storage/card_stories/form-{형태}.jsonl, 진행 상황은 form-{형태}-state.json. 끊겨도 다시 돌리면 남은 것만.

    python -m scripts.batch_form_stories --form quadruped --limit 10 --budget 1     # 시험
    python -m scripts.batch_form_stories --form quadruped --budget 40               # 512편
    python -m scripts.batch_form_stories --form quadruped --load [--approve-clean]  # 표에 넣기

형태 규칙(forms)은 AI 서버(AI_SERVICE_URL)에서 받는다. 대본은 Claude(ANTHROPIC_API_KEY), 내용 검사는 OpenAI moderation.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import anthropic

from app.core.config import get_settings
from app.services import card_stories as cs
from app.services import forms
from app.services.book import OPENINGS, book_prompt, book_user_message
from app.services.story_writer import get_story_writer
from scripts import batch_card_stories as B

OUT = Path("storage/card_stories")


def paths(form: str) -> tuple[Path, Path]:
    return OUT / f"form-{form}.jsonl", OUT / f"form-{form}-state.json"


def load(form: str) -> dict[str, dict]:
    rows_path, _ = paths(form)
    rows = {}
    if rows_path.exists():
        for line in rows_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[B.ckey(cs.Combo(r["place"], r["problem"], r["action"], r["result"]))] = r
    return rows


def request(c: cs.Combo, hero: dict) -> dict:
    s = get_settings()
    base = B.base_request(c)
    base["system"] = book_prompt(s.story_pages, False, None, None, c.place, hero)
    return base


def run(form: str, limit: int | None, budget: float) -> None:
    hero = forms.get(form)
    if (hero.get("id") or form) != form:
        raise SystemExit(f"AI 서버에서 형태 {form} 을(를) 받지 못했습니다")
    rows_path, state_path = paths(form)
    OUT.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"failed": {}, "spent": 0.0}
    rows = load(form)
    todo = [c for c in cs.combos() if B.ckey(c) not in rows and state["failed"].get(B.ckey(c), 0) < 3]
    if limit:
        todo = todo[:limit]
    per = B.AVG["base"][0] * B.PRICE_IN + B.AVG["base"][1] * B.PRICE_OUT
    if len(todo) * per > budget:
        todo = todo[: int(budget / per)]
    if not todo:
        B.log("보낼 것이 없습니다")
        return
    B.log(f"{form}: {len(todo)}편 보냄 (예상 ${len(todo) * per:.2f}, 지금까지 ${state['spent']:.2f})")
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
    batch = client.messages.batches.create(
        requests=[{"custom_id": B.ckey(c), "params": request(c, hero)} for c in todo])
    state.setdefault("batches", []).append(batch.id)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    results = B.wait(client, batch.id)
    writer = get_story_writer()
    ok = bad = 0
    spent = 0.0
    for res in results:
        k = res.custom_id
        c = B.COMBO_BY_KEY[k]
        if res.result.type != "succeeded":
            state["failed"][k] = state["failed"].get(k, 0) + 1
            bad += 1
            continue
        msg = res.result.message
        u = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
        spent += u["input"] * B.PRICE_IN + u["output"] * B.PRICE_OUT
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            payload, problems = B.read_base(c, text, writer)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            state["failed"][k] = state["failed"].get(k, 0) + 1
            bad += 1
            B.log(f"  형식 오류 {k}: {e}")
            continue
        if problems:
            state["failed"][k] = state["failed"].get(k, 0) + 1
            bad += 1
            B.log(f"  검사 걸림 {k}: {'; '.join(problems)}")
            continue
        r = B.row(c, "long", "narration", payload, problems, u,
                  opening=OPENINGS[cs.combos().index(c) % len(OPENINGS)], spot=cs.spot_for(c))
        r["form"] = form
        with rows_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        ok += 1
    state["spent"] = state.get("spent", 0) + spent
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    n = max(1, ok + bad)
    B.log(f"끝 · 저장 {ok} · 다시/실패 {bad} · 이번 ${spent:.2f} (한 편 ${spent / n:.3f}) · 누적 ${state['spent']:.2f}")


def load_db(form: str, approve_clean: bool) -> None:
    """jsonl → card_stories 표. 검사(형식 · 카드 · 내용)를 통과한 것만 jsonl 에 있으니 approve_clean 이면 바로 approved."""
    from app.db.session import SessionLocal
    from app.models.card_story import CardStory

    rows = load(form)
    with SessionLocal() as db:
        for r in rows.values():
            key = {k: r[k] for k in ("place", "problem", "action", "result", "length", "voice_mode")}
            row = db.query(CardStory).filter_by(**key, form=form).one_or_none() or CardStory(**key, form=form)
            row.payload, row.model, row.prompt_version = r["payload"], r["model"], r["prompt_version"]
            row.status = "approved" if approve_clean else "draft"
            row.reviewed_by = "auto-check" if approve_clean else None
            db.add(row)
        db.commit()
    B.log(f"{form}: {len(rows)}편을 {'approved' if approve_clean else 'draft'} 로 넣었습니다")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--approve-clean", action="store_true")
    a = ap.parse_args()
    start = time.time()
    if a.load:
        load_db(a.form, a.approve_clean)
    else:
        run(a.form, a.limit, a.budget)
