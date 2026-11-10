"""형태별(사람 아닌 주인공) 카드 이야기 씨앗 (2026-11-10).

운영 DB 는 VM 안 도커라 밖에서 쓸 수 없고 구조 · 내용 변경은 마이그레이션 파일로만 한다 (팀 규칙). 그래서 씨앗 파일
`app/seed/form_stories.jsonl.gz` 를 저장소에 두고, 마이그레이션(`alembic/versions/*_seed_form_card_stories.py`)이 배포 때
`apply` 로 표에 맞춘다 (같은 조합 · 형태가 있으면 씨앗 내용으로, 없으면 새로).

씨앗 만들기 (대본을 만든 사람만, 결과 파일은 storage/card_stories):
    python -m app.seed.form_stories --build
- 날개형 form-winged.jsonl (scripts/batch_form_stories 로 만든 512편): 검수 통과 · 검수 고침이고 몸 규칙 · 손 검사를 지나면 approved,
  아니면 draft (검수 메모를 note 로)
- 4족 · 사슬형 fixed-{형태}.jsonl (운영 대본 중 몸 규칙에 걸린 것을 틀린 쪽만 고친 것): 고친 것만 approved 로. 못 고친 것은 넣지 않는다
  (운영 대본 그대로)
- draft 고치기 fixed-drafts5*.jsonl: 재검수를 지난 것만 approved 로 (날개형 · 4족 draft 를 고친 것)
"""

from __future__ import annotations

import argparse
import gzip
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

SEED = Path(__file__).resolve().parent / "form_stories.jsonl.gz"
KEY = ("place", "problem", "action", "result", "length", "voice_mode", "form")
FIELDS = ("payload", "status", "model", "prompt_version", "reviewed_by", "note")
VERSION = "form-2026-11-10"


def read_seed(path: Path = SEED) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def apply(conn, rows: list[dict] | None = None) -> dict[str, int]:
    """씨앗을 card_stories 표에 맞춘다 (SQLAlchemy Core, 마이그레이션 안에서 op.get_bind() 로). 몇 번 돌려도 같다."""
    import sqlalchemy as sa

    typed = {"id": sa.Uuid(), "payload": sa.JSON(), "reviewed_at": sa.DateTime(timezone=True),
             "created_at": sa.DateTime(timezone=True), "updated_at": sa.DateTime(timezone=True)}
    t = sa.table("card_stories", *(sa.column(c, typed.get(c, sa.String())) for c in ("id", *KEY, *FIELDS, "reviewed_at", "created_at", "updated_at")))
    rows = read_seed() if rows is None else rows
    now = datetime.now(UTC)
    count = {"new": 0, "updated": 0, "same": 0}
    for d in rows:
        where = sa.and_(*(t.c[k] == d[k] for k in KEY))
        cur = conn.execute(sa.select(t.c.id, t.c.payload, t.c.status, t.c.note).where(where)).first()
        values = {k: d[k] for k in FIELDS}
        if cur is None:
            conn.execute(t.insert().values(id=uuid.uuid4(), **{k: d[k] for k in KEY}, **values,
                                           reviewed_at=now, created_at=now, updated_at=now))
            count["new"] += 1
            continue
        payload = cur.payload if isinstance(cur.payload, dict) else json.loads(cur.payload)
        if payload == d["payload"] and cur.status == d["status"] and cur.note == d["note"]:
            count["same"] += 1
            continue
        conn.execute(t.update().where(where).values(**values, reviewed_at=now, updated_at=now))
        count["updated"] += 1
    return count


def build(out: Path = SEED, storage: Path = Path("storage/card_stories")) -> dict[str, int]:
    """storage/card_stories 의 결과 → 씨앗 (늘 같은 순서, gzip 시각 0, 고친 것만 diff 에 보이게)"""
    from app.services import card_stories as cs
    from app.services.book import body_problems

    rows: list[dict] = []
    count: dict[str, int] = {}
    for line in (storage / "form-winged.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rv = r.get("review") or {}
        body = body_problems(cs.book_from_payload(r["payload"], None), {"id": "winged"})
        flagged = rv.get("verdict") not in (None, "pass") and not r.get("review_fixed")
        ok = not body and not flagged
        note = None if ok else ("검수 지적: " + "; ".join(f"{i.get('page')}쪽 {i.get('note')}" for i in rv.get("issues") or [])
                                if flagged else "몸 규칙: " + "; ".join(body))
        rows.append({**{k: r[k] for k in ("place", "problem", "action", "result", "length", "voice_mode")}, "form": "winged",
                     "payload": r["payload"], "status": "approved" if ok else "draft", "model": r.get("model") or "claude-opus-5-5",
                     "prompt_version": VERSION, "reviewed_by": "auto-check+opus-review" if ok else None, "note": note})
    for form in ("chain", "quadruped"):
        path = storage / f"fixed-{form}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line) if line.strip() else None
            if not r or not r.get("fixed"):
                continue
            rows.append({**{k: r[k] for k in ("place", "problem", "action", "result")}, "length": "long", "voice_mode": "narration",
                         "form": form, "payload": r["payload"], "status": "approved", "model": "claude-opus-5-5",
                         "prompt_version": VERSION, "reviewed_by": "auto-check",
                         "note": "몸 규칙 고침 (11/10): " + "; ".join(r.get("problems_before") or [])})
    # draft 고치기 (11/10 사용자 「draft 5편 고쳐」): fixed-drafts5*.jsonl 중 재검수를 지난 것은 approved 로 바꾼다 (나중 회차가 이긴다)
    at = {tuple(d[k] for k in KEY): i for i, d in enumerate(rows)}
    for path in sorted(storage.glob("fixed-drafts5*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line) if line.strip() else None
            if not r or (r.get("review") or {}).get("verdict") != "pass":
                continue
            d = {**{k: r[k] for k in ("place", "problem", "action", "result")}, "length": "long", "voice_mode": "narration",
                 "form": r["form"], "payload": r["payload"], "status": "approved", "model": "claude-opus-5-5",
                 "prompt_version": VERSION, "reviewed_by": "auto-check+opus-review",
                 "note": "draft 고침 (11/10): " + "; ".join(r.get("problems_before") or [])}
            key = tuple(d[k] for k in KEY)
            if key in at:
                rows[at[key]] = d
            else:
                at[key] = len(rows)
                rows.append(d)
    for d in rows:
        count[f"{d['form']}:{d['status']}"] = count.get(f"{d['form']}:{d['status']}", 0) + 1
    rows.sort(key=lambda d: tuple(d[k] for k in KEY))
    body = "".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in rows).encode()
    with open(out, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(body)
    return count


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="storage/card_stories 결과 → 씨앗 파일")
    a = ap.parse_args()
    if a.build:
        print(build())
