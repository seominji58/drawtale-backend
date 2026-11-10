"""형태별 카드 이야기 미리 만들기 — 4족형 · 사슬형 … (2026-11-09 사용자 「비휴머노이드는 대본 쓰는 시간이 길다」).

사람형 카드 이야기(batch_card_stories)와 같은 배치 API(반값) · 같은 검사를 쓰고, 지시문에 그 형태의 몸 규칙(hero_form)을 넣는다.
비휴머노이드 이야기는 지금 긴 판 · 나레이션만 쓰므로 기본판 하나만 만든다.
결과는 storage/card_stories/form-{형태}.jsonl, 진행 상황은 form-{형태}-state.json. 끊겨도 다시 돌리면 남은 것만.

    python -m scripts.batch_form_stories --form quadruped --limit 10 --budget 1     # 시험
    python -m scripts.batch_form_stories --form quadruped --budget 40               # 512편
    python -m scripts.batch_form_stories --form quadruped --load [--approve-clean]  # 표에 넣기
    python -m scripts.batch_form_stories --form quadruped --check [--from-db]        # 만든 대본을 지금 검사로 다시 (무료)
    python -m scripts.batch_form_stories --form chain --check --file X.jsonl; ... --fix-saved --file X.jsonl  # 걸린 것만 고치기

형태 규칙(forms)은 AI 서버(AI_SERVICE_URL)에서 받는다. 대본은 Claude(ANTHROPIC_API_KEY), 내용 검사는 OpenAI moderation.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import anthropic

from app.core.config import get_settings
from app.services import card_stories as cs
from app.services import forms
from app.services.book import (
    OPENINGS,
    body_problems,
    body_rules,
    book_problems,
    book_prompt,
    book_user_message,
    form_words,
)
from app.services.story_writer import REPAIR, get_story_writer, json_part, merge_repair
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


# 쓰는 모델 (11/10 날개형 20편 시험: Opus 검수 통과 Opus 10/19 · Sonnet 4/20). 배치라 반값 (백만 토큰당 입력, 출력)
MODELS = {"opus": ("claude-opus-5-5", (2.0, 10.0)), "sonnet": ("claude-sonnet-5-5", (1.0, 5.0))}
REVIEW_MODEL, REVIEW_PRICE = "claude-opus-5-5", (2.0, 10.0)
# 한 편 어림 (11/10 실측: 쓰기 · 절반쯤 고치기 · 검수 · 절반쯤 검수 고치기)
PER_STORY = {"opus": 0.09, "sonnet": 0.04}

# 검수 (11/10 사용자 「검수 단계도 붙여」). 자동 검사가 못 보는 것: 카드 뜻 · 몸 규칙 · 위험 · 장면 · 어색함.
# 지적이 있으면 그 쪽만 한 번 고친다 (REPAIR 와 같은 방식). 다시 검수하지는 않는다 (고친 뒤 자동 검사만)
REVIEW = """너는 느린 아이들을 위한 그림책 대본 검수자야. 아래 대본을 읽고 고칠 것만 찾는다. 사소한 말맛 차이는 넘어간다.

[카드] 장소 「{place}」, 문제 「{problem}」, 행동 「{action}」, 결과 「{result}」. 각 단계 쪽(stage)이 이 카드 뜻을 그대로 지켜야 한다 (뜻을 뒤집거나 비틀면 고칠 것)
[주인공 모습] 주인공 「나」는 {kind}이다. 아래 몸 규칙을 어기면 고칠 것:
{body}
[보는 것]
1. 카드 뜻: 행동 · 결과를 바꾸거나(도와줬어요 → 도움을 요청) 빠뜨림
2. 몸 규칙: 주인공이 할 수 없는 몸짓(손으로 잡기 · 손 흔들기 · 팔 …)
3. 위험 · 무서움: 아이가 따라 하면 다칠 일, 피 · 상처를 자세히, 무서운 장면
4. 장면 계획(scene): 행동 · 결과 쪽에 주인공이 빠짐, 대본에 없는 인물, 배경 지시문에 글자 · 간판 · 끊긴 문장
5. 어색함: 쪽 사이 이야기가 끊김, 같은 말 되풀이, 카드 글자를 따옴표 대사로 그대로 넣음
JSON 으로만 답한다: {{"verdict": "pass" 또는 "fix", "issues": [{{"page": 쪽 번호, "kind": "카드|몸|위험|장면|어색", "note": "짧게"}}]}}"""


def work_path(form: str) -> Path:
    """단계 사이에 남기는 것 (배치 id · 편마다 대본). 끊겨도 다시 돌리면 그 단계부터"""
    return OUT / f"form-{form}-work.json"


def check(c: cs.Combo, raw: str, hero: dict, writer) -> tuple[dict, list[str]]:
    """사람형 카드 이야기와 같은 검사(쪽 배치 · 카드 · 첫 쪽 자리 · 모를 어른 · 내용) + 형태 몸 규칙"""
    import openai

    for k in range(6):
        try:
            payload, problems = B.read_base(c, raw, writer)
            break
        except openai.RateLimitError:
            if k == 5:
                raise
            time.sleep(2 ** k * 2)
    return payload, problems + body_problems(cs.book_from_payload(payload, None), hero)


def fix_params(model: str, c: cs.Combo, hero: dict, raw: str, problems: list[str]) -> dict:
    """틀린 쪽(과 장면 계획)만 고쳐 받기 (story_writer.book_fix 와 같은 지시, effort low)"""
    base = request(c, hero)
    ask = REPAIR.format(problems="\n".join(f"- {x}" for x in problems))
    return {"model": model, "max_tokens": 8000, "system": base["system"], "output_config": {"effort": "low"},
            "messages": [base["messages"][0], {"role": "assistant", "content": raw}, {"role": "user", "content": ask}]}


def review_params(c: cs.Combo, hero: dict, payload: dict) -> dict:
    def scene(p: dict) -> str:
        sc = p.get("scene")
        if not sc:
            return ""
        who = [x.get("name") for x in sc.get("characters") or []]
        # 배경 지시문은 자르지 않는다: 160 자로 잘라 보냈더니 검수가 「배경 지시문이 끊김」을 20편 중 15편에 달았다 (11/10)
        return f"\n   장면: 주인공 {'있음' if sc.get('hero_on_page', True) else '없음'} · 인물 {who} · 배경 {sc.get('background', '')}"

    pages = payload["book"]["pages"]
    story = "\n".join(f"{p['page']}쪽 [{p['stage']}] {page_text(p)}{scene(p)}" for p in pages)
    system = REVIEW.format(place=c.place, problem=c.problem, action=c.action, result=c.result,
                           kind=form_words(hero)["kid_label"], body=body_rules(hero))
    return {"model": REVIEW_MODEL, "max_tokens": 6000, "system": system, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": f"제목: {payload['book'].get('title', '')}\n{story}"}]}


def run(form: str, limit: int | None, budget: float, model: str = "opus", review: bool = True) -> None:
    """쓰기 → 자동 검사 → 틀린 쪽만 고치기 → Opus 검수 → 지적한 쪽 고치기 (모두 배치, 11/10).
    전에는 검사에 걸리면 그 편을 버리고 다음에 처음부터 다시 썼다 (고치기보다 2~3배 비쌈)"""
    hero = forms.get(form)
    if (hero.get("id") or form) != form:
        raise SystemExit(f"AI 서버에서 형태 {form} 을(를) 받지 못했습니다")
    rows_path, state_path = paths(form)
    OUT.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"failed": {}, "spent": 0.0}
    wp = work_path(form)
    work = json.loads(wp.read_text(encoding="utf-8")) if wp.exists() else {}

    def save() -> None:
        wp.write_text(json.dumps(work, ensure_ascii=False), encoding="utf-8")
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    def fail(k: str, why: str) -> None:
        state["failed"][k] = state["failed"].get(k, 0) + 1
        work["items"].pop(k, None)
        B.log(f"  버림 {k}: {why}")

    def spend(msg, price: tuple[float, float]) -> float:
        cost = (msg.usage.input_tokens * price[0] + msg.usage.output_tokens * price[1]) / 1e6
        work["spent"] = work.get("spent", 0.0) + cost
        return cost

    def text_of(res) -> str | None:
        if res.result.type != "succeeded":
            return None
        return json_part("".join(b.text for b in res.result.message.content if b.type == "text"))

    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
    writer = get_story_writer()
    mname, mprice = MODELS[(work.get("model") or model)]

    # 1. 쓰기
    if "gen" not in work:
        rows = load(form)
        todo = [c for c in cs.combos() if B.ckey(c) not in rows and state["failed"].get(B.ckey(c), 0) < 3]
        if limit:
            todo = todo[:limit]
        per = PER_STORY[model] if review else PER_STORY[model] * 0.7
        if budget and len(todo) * per > budget:
            todo = todo[: int(budget / per)]
        if not todo:
            B.log("보낼 것이 없습니다")
            return
        B.log(f"{form}: {len(todo)}편 보냄 · {model} (예상 ${len(todo) * per:.2f}, 지금까지 ${state['spent']:.2f})")
        reqs = []
        for c in todo:
            params = request(c, hero)
            params["model"] = mname
            reqs.append({"custom_id": B.ckey(c), "params": params})
        work = {"gen": client.messages.batches.create(requests=reqs).id, "model": model, "review": review, "items": {}, "spent": 0.0}
        state.setdefault("batches", []).append(work["gen"])
        save()
    if not work.get("gen_done"):
        for res in B.wait(client, work["gen"]):
            k, c = res.custom_id, B.COMBO_BY_KEY[res.custom_id]
            raw = text_of(res)
            if raw is None:
                state["failed"][k] = state["failed"].get(k, 0) + 1
                continue
            spend(res.result.message, mprice)
            try:
                payload, problems = check(c, raw, hero, writer)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                state["failed"][k] = state["failed"].get(k, 0) + 1
                B.log(f"  형식 오류 {k}: {e}")
                continue
            u = res.result.message.usage
            work["items"][k] = {"raw": raw, "payload": payload, "problems": problems, "first": problems,
                                "usage": {"input": u.input_tokens, "output": u.output_tokens}}
        work["gen_done"] = True
        save()

    # 2. 틀린 쪽만 고치기 (쪽 배치가 틀린 것은 고치기로 안 된다, 버리고 다음에 다시 쓴다)
    for k in [k for k, it in work["items"].items() if any(x.startswith("쪽 배치") for x in it["problems"])]:
        fail(k, "쪽 배치")
    need = {k: it for k, it in work["items"].items() if it["problems"]}
    if need and "fix" not in work:
        work["fix"] = client.messages.batches.create(requests=[
            {"custom_id": k, "params": fix_params(mname, B.COMBO_BY_KEY[k], hero, it["raw"], it["problems"])}
            for k, it in need.items()]).id
        save()
    if need and not work.get("fix_done"):
        got = {r.custom_id: r for r in B.wait(client, work["fix"])}
        for k, it in need.items():
            res = got.get(k)
            fix = text_of(res) if res else None
            if fix is None:
                continue
            spend(res.result.message, mprice)
            try:
                raw = merge_repair(it["raw"], fix)
                it["payload"], it["problems"] = check(B.COMBO_BY_KEY[k], raw, hero, writer)
                it["raw"], it["fixed"] = raw, True
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                B.log(f"  고치기 형식 오류 {k}: {e}")
        work["fix_done"] = True
        save()
    for k in [k for k, it in work["items"].items() if it["problems"]]:
        fail(k, "; ".join(work["items"][k]["problems"]))

    # 3. Opus 검수 → 4. 지적한 쪽 고치기
    if work.get("review") and work["items"]:
        if "review_batch" not in work:
            work["review_batch"] = client.messages.batches.create(requests=[
                {"custom_id": k, "params": review_params(B.COMBO_BY_KEY[k], hero, it["payload"])}
                for k, it in work["items"].items()]).id
            save()
        if not work.get("review_done"):
            for res in B.wait(client, work["review_batch"]):
                it = work["items"].get(res.custom_id)
                txt = text_of(res)
                if it is None or txt is None:
                    continue
                spend(res.result.message, REVIEW_PRICE)
                try:
                    it["review"] = json.loads(txt)
                except json.JSONDecodeError:
                    it["review"] = {"verdict": "error", "issues": []}
            work["review_done"] = True
            save()
        flagged = {k: it for k, it in work["items"].items()
                   if (it.get("review") or {}).get("verdict") == "fix" and (it["review"].get("issues") or [])}
        if flagged and "rfix" not in work:
            work["rfix"] = client.messages.batches.create(requests=[
                {"custom_id": k, "params": fix_params(mname, B.COMBO_BY_KEY[k], hero, it["raw"],
                                                      [f"{i.get('page')}쪽 [{i.get('kind')}] {i.get('note')}" for i in it["review"]["issues"]])}
                for k, it in flagged.items()]).id
            save()
        if flagged and not work.get("rfix_done"):
            got = {r.custom_id: r for r in B.wait(client, work["rfix"])}
            for k, it in flagged.items():
                res = got.get(k)
                fix = text_of(res) if res else None
                if fix is None:
                    continue
                spend(res.result.message, mprice)
                try:
                    raw = merge_repair(it["raw"], fix)
                    payload, problems = check(B.COMBO_BY_KEY[k], raw, hero, writer)
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
                if not problems:          # 고친 것이 자동 검사를 지나야 바꾼다 (아니면 고치기 전 것 + 검수 지적 그대로 → draft)
                    it["raw"], it["payload"], it["review_fixed"] = raw, payload, True
            work["rfix_done"] = True
            save()

    # 5. 저장
    ok = 0
    with rows_path.open("a", encoding="utf-8") as f:
        for k, it in work["items"].items():
            c = B.COMBO_BY_KEY[k]
            r = B.row(c, "long", "narration", it["payload"], [], it["usage"], model=mname,
                      opening=OPENINGS[cs.combos().index(c) % len(OPENINGS)], spot=cs.spot_for(c))
            r.update(form=form, first_problems=it["first"], fixed=bool(it.get("fixed")),
                     review=it.get("review"), review_fixed=bool(it.get("review_fixed")))
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            ok += 1
    state["spent"] = state.get("spent", 0) + work.get("spent", 0.0)
    passed = sum((it.get("review") or {}).get("verdict") == "pass" or bool(it.get("review_fixed")) for it in work["items"].values())
    B.log(f"끝 · 저장 {ok} (검수 통과 · 고침 {passed}) · 이번 ${work.get('spent', 0):.2f} (한 편 ${work.get('spent', 0) / max(1, ok):.3f}) · 누적 ${state['spent']:.2f}")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    wp.unlink(missing_ok=True)


# 주인공(나)이 손 · 팔을 쓰는 글 — 사람 몸짓이라 4족형 · 사슬형에 맞지 않는다. 「손을 흔들어 웃게 했어요」 카드를 그대로
# 옮긴 것(4족 27 · 사슬 12편). approved 로 넣지 않고 draft 로 둬 그때 대본 AI 가 새로 쓴다
HERO_HAND = re.compile(r"나는[^.!?]{0,30}(손을 흔들|손으로|손에 들|손뼉|두 손|팔을)|(내 손|내 두 손|내 팔)")


def page_text(p: dict) -> str:
    return p["text"] if "text" in p else " ".join(line["text"] for line in p["lines"])


def moderate_slow(writer, text: str) -> list[str]:
    """내용 검사 (OpenAI moderation, 무료). 몇백 편을 한꺼번에 부르면 429 가 나서 쉬어 가며 다시 (11/10)"""
    import openai

    for k in range(6):
        try:
            return B.moderate(writer, text)
        except openai.RateLimitError:
            time.sleep(2 ** k * 2)
    return ["내용 검사를 못 함 (429)"]


def raw_from_payload(payload: dict) -> str:
    """저장본(payload) → 대본 JSON (쓰기 응답과 같은 모양). 저장한 대본을 틀린 쪽만 고치기로 보낼 때"""
    book = payload["book"]
    pages, scene = [], []
    for p in book["pages"]:
        pages.append({"page": p["page"], "stage": p["stage"], "action": p["action"], "child_words": p.get("child_words") or "",
                      "lines": [{"speaker": ln["speaker"], "text": ln["text"]} for ln in p["lines"]]})
        sc = p.get("scene")
        if sc:
            scene.append({"page": p["page"], "hero_on_page": sc.get("hero_on_page", True), "characters": sc.get("characters") or [],
                          "location": sc.get("location") or "", "background": sc.get("background") or ""})
    cast = [{"name": n, "type": t} for n, t in (payload.get("cast") or {}).items()]   # 인물 이름 · 종류 (배경 지시문의 이름은 글자가 아니다)
    return json.dumps({"title": book.get("title", ""), "protagonist": "나", "characters": cast, "pages": pages,
                       "order_pages": book.get("order_pages") or [], "scene": {"character_sheet": book.get("character_sheet") or {}, "pages": scene}},
                      ensure_ascii=False)


def fix_saved(form: str, file: str, model: str = "opus") -> None:
    """이미 만든 대본 중 --check 에 걸린 것을 틀린 쪽만 고친다 (11/10 사용자 「316편 고치기 진행해」, 운영 DB 4족 · 사슬형).
    --check --file 이 남긴 check-{형태}.json 의 걸림을 고칠 것으로 보낸다(배치, effort low). 고친 뒤 자동 검사를 다시 하고,
    결과는 storage/card_stories/fixed-{형태}.jsonl (원래 상태 · 고치기 전후 걸림). DB 에는 쓰지 않는다"""
    hero = forms.get(form)
    rows = {B.ckey(cs.Combo(r["place"], r["problem"], r["action"], r["result"])): r
            for r in (json.loads(line) for line in Path(file).read_text(encoding="utf-8").splitlines() if line.strip())}
    # 내용 검사 · 형식 오류는 고치기로 보낼 일이 아니다 (나머지 걸림만)
    flagged = {f["key"]: probs for f in json.loads((OUT / f"check-{form}.json").read_text(encoding="utf-8"))
               if (probs := [x for x in f["problems"] if "내용 검사" not in x and "형식 오류" not in x])}
    mname, mprice = MODELS[model]
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
    writer = get_story_writer()
    wp = OUT / f"fix-{form}-work.json"
    work = json.loads(wp.read_text(encoding="utf-8")) if wp.exists() else {}
    if "batch" not in work:
        reqs = [{"custom_id": k, "params": fix_params(mname, B.COMBO_BY_KEY[k], hero, raw_from_payload(rows[k]["payload"]), probs)}
                for k, probs in flagged.items()]
        B.log(f"{form}: {len(reqs)}편 고치기 보냄 · {model}")
        work = {"batch": client.messages.batches.create(requests=reqs).id}
        wp.write_text(json.dumps(work), encoding="utf-8")
    spent, ok = 0.0, 0
    out = []
    for res in B.wait(client, work["batch"]):
        k = res.custom_id
        r, before = rows[k], flagged.get(k, [])
        rec = {**{f: r[f] for f in ("place", "problem", "action", "result", "status")}, "form": form,
               "problems_before": before, "fixed": False, "payload": r["payload"]}
        if res.result.type == "succeeded":
            msg = res.result.message
            spent += (msg.usage.input_tokens * mprice[0] + msg.usage.output_tokens * mprice[1]) / 1e6
            try:
                raw = merge_repair(raw_from_payload(r["payload"]), json_part("".join(b.text for b in msg.content if b.type == "text")))
                payload, after = check(B.COMBO_BY_KEY[k], raw, hero, writer)
                rec["problems_after"] = after
                if not after:
                    rec["payload"], rec["fixed"] = payload, True
                    ok += 1
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                rec["problems_after"] = [f"고치기 형식 오류 {e}"]
        out.append(rec)
    path = OUT / f"fixed-{form}.jsonl"
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    wp.unlink(missing_ok=True)
    B.log(f"{form}: 고침 {ok}/{len(out)} · ${spent:.2f} → {path}")


def check_saved(form: str, from_db: bool, file: str | None = None, moderation: bool = True) -> None:
    """이미 만든 대본을 지금 검사로 다시 본다 (11/10 사용자 「기존 만들어 둔 비휴머노이드 대본들 검사해 봐」).
    jsonl(storage/card_stories/form-{형태}.jsonl) 또는 --from-db 면 card_stories 표. 검사는 쓰기와 같다(쪽 배치 · 카드 · 첫 쪽 자리 ·
    모를 어른 · 내용 · 몸 규칙). 무료 (내용 검사는 OpenAI moderation). 결과는 storage/card_stories/check-{형태}.json"""
    hero = forms.get(form)
    if from_db:
        from app.db.session import SessionLocal
        from app.models.card_story import CardStory

        with SessionLocal() as db:
            rows = [{"place": r.place, "problem": r.problem, "action": r.action, "result": r.result, "payload": r.payload,
                     "status": r.status} for r in db.query(CardStory).filter_by(form=form, length="long", voice_mode="narration")]
    elif file:   # 다른 곳에서 꺼낸 jsonl (운영 DB 를 SSH 로 읽은 것 등, 줄마다 카드 네 칸 · payload · status)
        rows = [json.loads(line) for line in Path(file).read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = list(load(form).values())
    writer = get_story_writer()
    out, kinds = [], {}
    for r in rows:
        c = cs.Combo(r["place"], r["problem"], r["action"], r["result"])
        try:
            # 저장본(payload)에서 Book 을 다시 만들어 read_base 와 같은 검사 + 몸 규칙
            book = cs.book_from_payload(r["payload"], None)
            choices = {"place": c.place, "problem": c.problem, "action": c.action, "result": c.result}
            problems = book_problems(book, choices, get_settings().story_pages, False, hero)
            if B.spot_missing(c, book.pages[0].text()):
                problems.append(f"첫 쪽에 자리 「{cs.spot_for(c)}」가 없다")
            if B.UNKNOWN_ADULT.search(book.text()) and not B.KNOWN_ROLE.search(book.text()):
                problems.append("누구인지 모를 어른")
            if moderation:
                problems += moderate_slow(writer, book.text())
        except (ValueError, KeyError, TypeError) as e:
            problems = [f"형식 오류 {e}"]
        for x in problems:
            k = x.split(":")[0].split("쪽 ", 1)[-1] if "몸 규칙" in x else x.split(" ")[0]
            kinds[k] = kinds.get(k, 0) + 1
        if problems:
            out.append({"key": B.ckey(c), "status": r.get("status"), "problems": problems})
    path = OUT / f"check-{form}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    B.log(f"{form}: {len(rows)}편 중 걸림 {len(out)} · 종류 {kinds} → {path}")


def load_db(form: str, approve_clean: bool) -> None:
    """jsonl → card_stories 표. 검사(형식 · 카드 · 내용)를 통과한 것만 jsonl 에 있으니 approve_clean 이면 바로 approved.
    단 주인공이 손 · 팔을 쓰는 글(HERO_HAND)과 몸 규칙에 걸리는 글(book.body_problems, 11/10)은 draft"""
    from app.db.session import SessionLocal
    from app.models.card_story import CardStory

    rows = load(form)
    counts: dict[str, int] = {}
    with SessionLocal() as db:
        for r in rows.values():
            key = {k: r[k] for k in ("place", "problem", "action", "result", "length", "voice_mode")}
            row = db.query(CardStory).filter_by(**key, form=form).one_or_none() or CardStory(**key, form=form)
            row.payload, row.model, row.prompt_version = r["payload"], r["model"], r["prompt_version"]
            hand = form != "biped" and (HERO_HAND.search(" ".join(page_text(p) for p in r["payload"]["book"]["pages"]))
                                        or body_problems(cs.book_from_payload(r["payload"], None), {"id": form}))
            rv = r.get("review") or {}
            flagged = rv.get("verdict") not in (None, "pass") and not r.get("review_fixed")   # 검수 지적이 남음
            ok = approve_clean and not hand and not flagged
            row.status = "approved" if ok else "draft"
            row.reviewed_by = "auto-check" if ok else None
            row.note = ("주인공이 손 · 팔을 쓰거나 걸음, 이 형태에 맞지 않음" if hand
                        else "검수 지적: " + "; ".join(f"{i.get('page')}쪽 {i.get('note')}" for i in rv.get("issues") or []) if flagged else None)
            counts[row.status] = counts.get(row.status, 0) + 1
            db.add(row)
        db.commit()
    B.log(f"{form}: {len(rows)}편을 넣었습니다 {counts}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--approve-clean", action="store_true")
    ap.add_argument("--model", choices=sorted(MODELS), default="opus")
    ap.add_argument("--no-review", action="store_true")
    ap.add_argument("--check", action="store_true", help="이미 만든 대본을 지금 검사로 다시 보기 (무료)")
    ap.add_argument("--from-db", action="store_true", help="--check 를 card_stories 표에서")
    ap.add_argument("--file", help="--check 를 이 jsonl 에서")
    ap.add_argument("--no-moderate", action="store_true", help="--check 에서 내용 검사(OpenAI) 빼기")
    ap.add_argument("--fix-saved", action="store_true", help="--file 의 대본 중 --check 에 걸린 것을 틀린 쪽만 고치기 (배치)")
    a = ap.parse_args()
    start = time.time()
    if a.fix_saved:
        fix_saved(a.form, a.file, a.model)
    elif a.check:
        check_saved(a.form, a.from_db, a.file, not a.no_moderate)
    elif a.load:
        load_db(a.form, a.approve_clean)
    else:
        run(a.form, a.limit, a.budget, a.model, not a.no_review)
