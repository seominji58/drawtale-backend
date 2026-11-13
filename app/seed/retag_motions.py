"""카드 이야기(긴 판) 쪽 동작을 AI 로 다시 고른다 — 씨앗 파일에 한 번 저장 (2026-11-04, pt_docs 17).

카드 이야기 1,024편은 동작이 여섯 개뿐이던 때 만들어 쪽 45%가 가만히(idle)였다. 쪽 동작이 15개가 되어
쪽 글 · 단계 · 앞뒤 흐름을 보고 다시 고른다. 낱말 규칙(page_motions.liven)은 「찾아왔어요」를 땅 살피기로,
「무섭지 않았어요」를 떨기로 고르는 등 문맥을 몰라서. 이야기를 만들 때마다 부르지 않고 씨앗에 저장해 두므로
운영 비용 · 대기 시간이 늘지 않는다. 배포하면 remote-deploy.sh 가 씨앗을 DB 에 맞춘다.

    python -m app.seed.retag_motions --limit 5 --dry   # 다섯 편만 골라 보여 주기 (저장 안 함)
    python -m app.seed.retag_motions                   # 전부 (이미 고른 것은 건너뜀) → 씨앗 파일 다시 쓰기
"""

from __future__ import annotations

import argparse
import gzip
import json
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

from app.core.config import get_settings
from app.seed.card_stories import SEED, read_seed
from app.services.page_motions import NEW_ACTIONS

MODEL = "gpt-5.4-mini"   # 대본 모델과 같은 것 (config.story_book_model)
TAG = "2026-11-04"       # 다시 고른 표시 (payload.book.motion_tag). 이 값이면 건너뛴다
OLD = {"walk": "걷기 — 옮겨 가는 쪽", "run": "뛰기 — 서둘러 옮겨 가는 쪽", "jump": "제자리 점프",
       "wave": "손 흔들기 — 인사 · 부르기 · 부탁", "dance": "춤"}
ALLOWED = {**OLD, **NEW_ACTIONS}

SYSTEM = """너는 그림책 연출가다. 아이가 그린 주인공 그림이 쪽마다 하는 몸 동작을 하나씩 고른다.
그림은 평면이라 동작은 아래 목록에서만 고른다:
{allowed}

- **주인공**이 그 쪽에서 하는 일과 기분에 가장 맞는 것. 엄마 · 친구 · 동물 같은 다른 인물이 하는 일은 보지 않는다
  (「엄마가 달려왔어요」는 주인공 run 이 아니다).
- 옮겨 가는 쪽만 walk · run (무엇을 따라가거나 쫓아가는 것도 walk · run). 주인공 자신이 실제로 공을 차거나
  헤엄치거나 하늘을 나는 쪽만 kick · swim · fly — 나비 · 새가 날아도 주인공은 fly 가 아니다.
- 「무섭지 않았어요」처럼 아니라고 하는 말은 그 동작이 아니다.
- 마음이 졸이거나 무섭거나 추운 쪽은 shiver, 무엇을 찾거나 둘러보는 쪽은 search, 기쁘거나 해낸 쪽은 hooray · jump · dance,
  무엇을 보여 주거나 「저기!」 하는 쪽은 point. 가만히 듣거나 기다리는 쪽만 breathe — breathe 는 한 이야기에 세 쪽까지.
- 같은 동작을 세 쪽 넘게 잇지 않는다.
- breathe · walk · run · jump · wave · dance 말고는 한 이야기에 세 가지까지만 쓴다 (서버가 그때마다 그린다).
JSON 으로만: {{"actions": ["…", …]}} — 쪽 수와 같은 길이, 쪽 순서대로."""


def _system() -> str:
    return SYSTEM.format(allowed="\n".join(f"  {k}: {v}" for k, v in ALLOWED.items()))


def _user(row: dict) -> str:
    pages = row["payload"]["book"]["pages"]
    body = "\n".join(
        f"{p['page']}쪽 [{p['stage']}] (지금 {p['action']}) "
        + " ".join(ln["text"] for ln in p["lines"]).replace("{이름}", "주인공")
        for p in pages
    )
    return f"카드: {row['place']} / {row['problem']} / {row['action']} / {row['result']}\n\n{body}"


def problems(actions: list, n: int) -> list[str]:
    """고른 것이 규칙에 맞지 않으면 그 이유들."""

    out = []
    if not isinstance(actions, list) or len(actions) != n:
        return [f"쪽 수 {n}개와 길이가 다르다"]
    bad = [a for a in actions if a not in ALLOWED]
    if bad:
        out.append(f"목록에 없는 동작 {bad}")
    if any(actions[i] == actions[i + 1] == actions[i + 2] == actions[i + 3] for i in range(n - 3)):
        out.append("같은 동작이 세 쪽 넘게 이어진다")
    if actions.count("breathe") > 3:
        out.append("breathe 가 세 쪽을 넘는다")
    fresh = {a for a in actions if a in NEW_ACTIONS and a != "breathe"}
    if len(fresh) > 3:
        out.append(f"새 동작이 세 가지를 넘는다 {sorted(fresh)}")
    return out


def pick(client: OpenAI, row: dict) -> list[str] | None:
    """쪽 동작들. 두 번 고쳐도 규칙에 안 맞으면 None (그 이야기는 그대로 — 화면에 갈 때 낱말 규칙이 보정)."""

    n = len(row["payload"]["book"]["pages"])
    messages = [{"role": "system", "content": _system()}, {"role": "user", "content": _user(row)}]
    for _ in range(3):
        res = client.chat.completions.create(model=MODEL, messages=messages, response_format={"type": "json_object"})
        text = res.choices[0].message.content or "{}"
        try:
            actions = json.loads(text).get("actions")
        except json.JSONDecodeError:
            actions = None
        why = problems(actions, n)
        if not why:
            return actions
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": "고쳐 줘: " + " · ".join(why)}]
    return None


def write_seed(rows: list[dict]) -> None:
    """card_stories.export_seed 와 같은 모양 (순서 그대로, gzip 시각 0) — 바뀐 것만 diff 에 보이게."""

    body = "".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in rows).encode()
    with open(SEED, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 몇 편만")
    ap.add_argument("--dry", action="store_true", help="저장하지 않고 보여 주기만")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    client = OpenAI(api_key=get_settings().openai_api_key, timeout=120)
    rows = read_seed()
    todo = [r for r in rows if r["length"] == "long" and r["payload"]["book"].get("motion_tag") != TAG]
    if a.limit:
        todo = todo[: a.limit]

    def one(row: dict) -> tuple[dict, list[str] | None]:
        try:
            return row, pick(client, row)
        except Exception as e:  # noqa: BLE001 — 한 편이 실패해도 나머지는 간다
            print("실패", row["place"], row["problem"], e, flush=True)
            return row, None

    done = failed = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for row, actions in ex.map(one, todo):
            pages = row["payload"]["book"]["pages"]
            if actions is None:
                failed += 1
                continue
            if a.dry:
                print(f"\n== {row['place']} / {row['problem']} / {row['action']} / {row['result']}")
                for p, new in zip(pages, actions, strict=True):
                    text = " ".join(ln["text"] for ln in p["lines"])[:60]
                    print(f"  {p['page']}쪽 {p['stage']:7s} {p['action']:5s} → {new:8s} {text}")
            for p, new in zip(pages, actions, strict=True):
                p["action"] = new
            row["payload"]["book"]["motion_tag"] = TAG
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(todo)}", flush=True)
    print(f"고름 {done} · 실패 {failed} (실패는 그대로 두고 낱말 규칙이 보정)")
    if not a.dry and done:
        write_seed(rows)
        print("씨앗 파일을 다시 썼습니다:", SEED)


if __name__ == "__main__":
    main()
