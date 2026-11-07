"""AI 검토가 짚은 것만 고쳐 쓰기 — 사람이 2,048편을 다 읽지 않도록 (2026-10-29 사용자).

    python -m scripts.fix_card_stories export <폴더>     # 고쳐 쓸 요청을 requests/ 에 (클라우드 세션 · 배치 어디서든)
    python -m scripts.cloud_card_stories import <폴더>   # 답 들이기 — batch_card_stories.process 가 fix* 를 안다

무엇을 고치나: 지금 이야기와 지문이 같은 AI 판정(review_ai.jsonl)의 지적 + 자동 검사에 걸린 채 저장된 것.
캐릭터 판의 대사 표시 형식을 짚은 것(오탐, 10/28 647건 중 310건)은 뺀다.
어디를 고치나:
  - 기본판(긴 판 · 나레이션) 또는 「all」을 짚었으면 → 기본판을 고친다(fixbase). 나머지 세 판은 고친 기본판에서 다시 고쳐 쓴다
    (기본판이 바뀌면 batch_card_stories.plan 이 나머지 판을 할 일로 잡는다 — cloud_card_stories export 로 이어서)
  - 나머지 판만 짚었으면 → 그 판만 고친다(fixlc · fixsn · fixsc)
고쳐 쓰기는 「고칠 점」만 고치고 나머지는 그대로 둔다. 답 형식은 처음 만들 때와 같아서 같은 검사를 거친다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.core.config import get_settings
from app.services import card_variants as cv
from app.services.book import book_prompt
from app.services.voices import NARRATOR, PROTAGONIST
from scripts import batch_card_stories as bcs
from scripts import review_card_stories as rvs

FMT_FP = re.compile(r"화자 표시|말하는 사람 표시|\[주인공\]|대사 표시|대사가 (문장 )?(한가운데|중간)|끼어|목소리로 바뀌|두 목소리|목소리가 바뀌")

FIX_RULE = """

[고쳐 쓰기 — 이번에 할 일]
아래 「지금 대본」은 위 규칙으로 이미 쓴 이야기다. 사람 대신 검토한 편집자가 「고칠 점」을 짚었다.
- **고칠 점만 고친다.** 나머지 쪽 · 장면 · 말 · 첫 쪽 자리 · 배경 계획(scene)은 그대로 둔다.
- 고칠 점이 다른 규칙과 부딪히면 위 규칙이 먼저다. 카드 뜻 · 쪽 배치는 바꾸지 않는다.
- 답은 위 형식 그대로, 고친 이야기 **전체**를 쓴다."""

FIX_RULE_DERIVED = """

[고쳐 쓰기 — 이번에 할 일]
아래 「지금 글」은 위 기본판을 고쳐 쓴 판이다. 편집자가 「고칠 점」을 짚었다.
- **고칠 점만 고친다.** 기본판과 같은 이야기(줄거리 · 인물 · 결말)로 둔다.
- 캐릭터 판은 한 문장 안에 나레이션과 대사가 같이 드는 형식이다(순서 맞추기 카드가 문장 단위라 일부러 그렇게 만들었다).
  이 형식을 바꾸라는 지적(「문장 중간에 대사가 끼어 어색」 · 「목소리가 바뀌어 끊김」)은 따르지 않고, 같은 지적에 섞인 다른 문제만 고친다.
- 답은 위 형식 그대로, 고친 글 **전체**를 쓴다."""


def issues_text(issues: list[dict]) -> str:
    out = []
    for i in issues:
        q = f" — 「{i['quote']}」" if i.get("quote") else ""
        out.append(f"- [{i.get('type', '기타')}] {i.get('why', '')}{q}")
    return "\n".join(out)


def api_book(payload: dict, hero: str) -> dict:
    """저장본 → 모델이 처음 답했던 JSON 모양 (고쳐 쓰기에서 「지금 대본」으로 보여 준다)."""
    b = payload["book"]
    return {
        "title": b["title"], "protagonist": hero,
        "characters": [{"name": n, "type": t} for n, t in (payload.get("cast") or {}).items()],
        "pages": [{"page": p["page"], "stage": p["stage"], "action": p["action"], "child_words": p.get("child_words") or "",
                   "lines": [{k: v for k, v in ln.items() if v} for ln in p["lines"]]} for p in b["pages"]],
        "order_pages": b["order_pages"],
        "scene": {"character_sheet": b.get("character_sheet") or {},
                  "pages": [{"page": p["page"], **(p.get("scene") or {})} for p in b["pages"]]},
    }


def current_text(kind: tuple[str, str], payload: dict) -> str:
    """지금 글 — 이름 자리는 자리 이름 「라라」로 보여 준다 (표시를 그대로 따라 쓰지 않게)."""
    return bcs.cs.untemplatize(_current_text(kind, payload))


def _current_text(kind: tuple[str, str], payload: dict) -> str:
    if kind == ("long", "characters"):
        b = api_book(payload, bcs.cs.SLOT_NAME)
        return json.dumps({"title": b["title"], "protagonist": bcs.cs.SLOT_NAME, "characters": b["characters"],
                           "pages": [{"page": p["page"], "lines": p["lines"]} for p in b["pages"]]}, ensure_ascii=False, indent=1)
    if kind == ("short", "narration"):
        return payload["text"]
    return json.dumps({"protagonist": bcs.cs.SLOT_NAME, "sentences": [
        [{"speaker": s, "text": t, **({"emotion": e} if e else {})} for s, t, e in parts] for parts in payload["sentences"]]},
        ensure_ascii=False, indent=1)


def targets(rows: dict) -> dict[str, list[dict]]:
    """custom_id → 고칠 점 목록."""
    done = rvs.load_results()
    out: dict[str, list[dict]] = {}
    for k in bcs.COMBO_BY_KEY:
        res = done.get(k)
        issues = []
        if res and res.get("fingerprint") == rvs.fingerprint(k, rows):
            issues = [i for i in res["issues"] if not FMT_FP.search(i.get("why", "") + i.get("quote", ""))]
        # 자동 검사에 걸린 채 저장된 것
        for (length, mode), ver in ((("long", "narration"), "long-narration"), (("long", "characters"), "long-characters"),
                                    (("short", "narration"), "short-narration"), (("short", "characters"), "short-characters")):
            r = rows.get((k, length, mode))
            for p in (r or {}).get("problems") or []:
                why = "내용 검사(폭력)에 걸렸다 — 넘어짐 · 다침 · 무서운 말을 가볍게" if "내용 검사" in p else p
                issues.append({"version": ver, "type": "자동 검사", "why": why})
        if not issues:
            continue
        vers = {i.get("version") for i in issues}
        if vers & {"long-narration", "all", None}:
            out[f"fixbase-{k}"] = issues  # 기본판을 고치면 나머지는 다시 고쳐 쓴다
            continue
        for ver, code in (("long-characters", "fixlc"), ("short-narration", "fixsn"), ("short-characters", "fixsc")):
            mine = [i for i in issues if i.get("version") == ver]
            if mine:
                out[f"{code}-{k}"] = mine
    return out


def export(folder: Path) -> None:
    rows = bcs.load_rows()
    todo = targets(rows)
    # 이미 이번 회차로 고친 것은 다시 내보내지 않는다
    todo = {cid: v for cid, v in todo.items() if not bcs.already_saved(rows, *cid.split("-", 1))}
    out = folder / "requests"
    out.mkdir(parents=True, exist_ok=True)
    (folder / "responses").mkdir(exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    pages = get_settings().story_pages
    for cid, issues in todo.items():
        code, k = cid.split("-", 1)
        c = bcs.COMBO_BY_KEY[k]
        base = rows[(k, "long", "narration")]["payload"]
        cards = f"장소 「{c.place}」 · 문제 「{c.problem}」 · 행동 「{c.action}」 · 결과 「{c.result}」"
        if code == "fixbase":
            system = book_prompt(pages, False, None) + FIX_RULE
            user = (f"아이가 고른 카드: {cards}\n첫 쪽 자리: {c.place}의 「{bcs.cs.spot_for(c)}」\n\n"
                    f"지금 대본\n{json.dumps(api_book(base, '나'), ensure_ascii=False, indent=1)}\n\n고칠 점\n{issues_text(issues)}")
        else:
            kind = bcs.FIX_CODES[code]
            system, user0, _ = cv.request(kind, c, base)
            system += FIX_RULE_DERIVED
            cur = rows[(k, *kind)]["payload"]
            user = f"{user0}\n\n지금 글\n{current_text(kind, cur)}\n\n고칠 점\n{issues_text(issues)}"
        (out / f"{cid}.json").write_text(json.dumps({"id": cid, "system": system, "user": user}, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds: dict[str, int] = {}
    for cid in todo:
        kinds[cid.split("-")[0]] = kinds.get(cid.split("-")[0], 0) + 1
    print(f"고쳐 쓸 요청 {len(todo)}개 {kinds} → {out}")


if __name__ == "__main__":
    if sys.argv[1] == "export":
        export(Path(sys.argv[2]))
    else:
        sys.exit("export <폴더> 만 있다. 들이기는 python -m scripts.cloud_card_stories import <폴더>")
