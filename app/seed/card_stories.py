"""미리 만든 카드 이야기 씨앗 — 저장소에 넣어 두고 어느 DB 에든 넣는다 (2026-10-29).

카드 이야기 2,048편(조합 512 × 길이 2 × 읽어주기 2)은 만든 사람의 로컬 DB 에만 있었다. 팀원 로컬 · 배포 DB 가
같은 이야기를 쓰도록, 통과(approved)한 것을 `app/seed/card_stories.jsonl.gz` 로 저장소에 둔다.
배포 이미지에는 app/ 만 들어가므로 app/ 안의 seed 폴더에 둔다.

    python -m app.seed.card_stories            # 씨앗 → DB (마이그레이션 뒤에. 몇 번 돌려도 같다)
    python -m app.seed.card_stories --export   # DB 의 approved → 씨앗 (이야기를 고친 사람만)
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime
from pathlib import Path

SEED = Path(__file__).resolve().parent / "card_stories.jsonl.gz"
KEY = ("place", "problem", "action", "result", "length", "voice_mode")
FIELDS = ("payload", "status", "model", "prompt_version", "reviewed_by", "note")


def _row(r) -> dict:
    d = {k: getattr(r, k) for k in KEY + FIELDS}
    d["reviewed_at"] = r.reviewed_at.isoformat() if r.reviewed_at else None
    return d


def export_seed(db, path: Path = SEED) -> int:
    """approved 만, 늘 같은 순서로 (고친 것만 diff 에 보이게). gzip 시각도 0 으로 고정."""

    from app.models.card_story import CardStory

    rows = sorted((_row(r) for r in db.query(CardStory).filter_by(status="approved")),
                  key=lambda d: tuple(d[k] for k in KEY))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in rows).encode()
    with open(path, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(body)
    return len(rows)


def read_seed(path: Path = SEED) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_seed(db, path: Path = SEED) -> dict[str, int]:
    """씨앗을 표에 넣는다. 같은 조합 · 판이 있으면 씨앗 내용으로 맞춘다 (새로 · 바뀜 · 같음 수)."""

    from app.models.card_story import CardStory

    count = {"new": 0, "updated": 0, "same": 0}
    for d in read_seed(path):
        key = {k: d[k] for k in KEY}
        row = db.query(CardStory).filter_by(**key).one_or_none()
        if row is None:
            row = CardStory(**key)
            db.add(row)
            count["new"] += 1
        elif all(getattr(row, k) == d[k] for k in FIELDS):
            count["same"] += 1
            continue
        else:
            count["updated"] += 1
        for k in FIELDS:
            setattr(row, k, d[k])
        row.reviewed_at = datetime.fromisoformat(d["reviewed_at"]) if d.get("reviewed_at") else None
    db.commit()
    return count


if __name__ == "__main__":
    from app.db.session import SessionLocal

    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="DB 의 approved 를 씨앗 파일로")
    a = ap.parse_args()
    with SessionLocal() as db:
        if a.export:
            print(f"씨앗에 {export_seed(db)}편을 썼습니다: {SEED}")
        else:
            print(f"씨앗을 넣었습니다: {load_seed(db)}")
