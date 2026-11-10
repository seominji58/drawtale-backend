"""reapply form card story seed (다시 쓴 5편: 날개형 draft 3 · 사슬형 못 고친 2)

날개형 draft 3편(글에 나오는 엄마가 인물 목록에 없어 틀린 쪽 고치기로 안 고쳐짐)과, 고치기에 실패해 원래 대본이 approved 로
나가던 사슬형 2편(「앉」 · 「안고」)을 scripts/batch_form_stories 로 그 조합만 처음부터 다시 썼다 (2026-11-10 사용자 「2번 진행」).
5편 모두 자동 검사 · Opus 검수(검수 고침 포함)를 지나 씨앗(app/seed/form_stories.jsonl.gz)에 넣고 다시 맞춘다 (바뀐 줄만).

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-11-10 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WINGED_DRAFTS = [
    ("놀이터", "어두워졌어요", "가만히 기다렸어요", "해가 떴어요"),
    ("놀이터", "어두워졌어요", "밝은 곳으로 달려갔어요", "잠이 들었어요"),
    ("마을", "어두워졌어요", "가만히 기다렸어요", "용기가 생겼어요"),
]


def upgrade() -> None:
    from app.seed import form_stories

    print("form card stories seed:", form_stories.apply(op.get_bind()))


def downgrade() -> None:
    # 날개형 3편만 draft 로 돌린다. 사슬형 2편의 예전 대본은 씨앗에 없어 다시 쓴 그대로 둔다
    conn = op.get_bind()
    for p, pr, a, r in WINGED_DRAFTS:
        conn.execute(sa.text("UPDATE card_stories SET status = 'draft' WHERE form = 'winged' AND place = :p AND problem = :pr "
                             "AND action = :a AND result = :r AND length = 'long' AND voice_mode = 'narration'"),
                     {"p": p, "pr": pr, "a": a, "r": r})
