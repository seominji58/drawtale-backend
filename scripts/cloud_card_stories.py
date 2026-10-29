"""카드 이야기를 클라우드 세션(Claude Code --cloud)에서 쓰게 하기 — API 크레딧 대신 클라우드 크레딧 (2026-10-28).

    python -m scripts.cloud_card_stories export <폴더>     # 지금 할 일(batch_card_stories 의 plan 과 같음)을 요청 파일로
    python -m scripts.cloud_card_stories import <폴더>     # 클라우드가 쓴 답을 API 결과와 똑같이 검사해 all.jsonl 에 넣는다

요청 파일 requests/<id>.json = {"id", "system", "user"} — API 배치에 보낼 지시문 · 메시지와 글자 하나 다르지 않다.
클라우드 세션은 system 을 자기 지시문으로 삼아 user 에 답하고, 답 글만 responses/<id>.txt 에 쓴다.
들여오기는 batch_card_stories.process 를 그대로 쓴다 — 검사(카드 뜻 · 쪽 배치 · 자리 · 내용 검사) · 저장 · 다시 보낼 것이 같다.
기본판이 바뀌면 나머지 판이 다시 할 일이 되므로 export → import 를 두 번(기본판, 나머지 판) 한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts import batch_card_stories as bcs
from app.services.story_writer import get_story_writer


def export(folder: Path, both: bool = False) -> None:
    state = json.loads(bcs.STATE.read_text(encoding="utf-8")) if bcs.STATE.exists() else {"failed": {}}
    rows = bcs.load_rows()
    stage1, stage2 = bcs.plan(rows, state.get("failed", {}))
    # both: 기본판 다시 쓰기와, 이미 확정된 기본판의 나머지 판을 한 번에 (기본판이 다시 나가는 조합의 나머지 판은 빠진다)
    reqs = {**stage1, **stage2} if both else (stage1 or stage2)
    out = folder / "requests"
    out.mkdir(parents=True, exist_ok=True)
    (folder / "responses").mkdir(exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    for cid, params in reqs.items():
        system = params["system"] if isinstance(params["system"], str) else "".join(b["text"] for b in params["system"])
        user = params["messages"][0]["content"]
        (out / f"{cid}.json").write_text(json.dumps({"id": cid, "system": system, "user": user}, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {}
    for cid in reqs:
        kinds[cid.split("-")[0]] = kinds.get(cid.split("-")[0], 0) + 1
    print(f"요청 {len(reqs)}개 {kinds} → {out}")


def _result(cid: str, text: str):
    msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=SimpleNamespace(input_tokens=0, output_tokens=0))
    return SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=msg))


def import_(folder: Path) -> None:
    state = json.loads(bcs.STATE.read_text(encoding="utf-8")) if bcs.STATE.exists() else {}
    state.setdefault("failed", {})
    results = [_result(p.stem, p.read_text(encoding="utf-8")) for p in sorted((folder / "responses").glob("*.txt"))]
    ok, bad, _ = bcs.process(results, state, get_story_writer())
    bcs.save_state(state)
    print(f"들여옴 {len(results)} · 저장 {ok} · 다시/걸림 {bad} (다시 export 하면 걸린 것만 다시 나간다)")


if __name__ == "__main__":
    cmd, folder = sys.argv[1], Path(sys.argv[2])
    if cmd == "export":
        export(folder, both="--both" in sys.argv)
    else:
        import_(folder)
