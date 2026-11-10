"""reapply form card story seed (draft 고친 2편: 날개형 1 · 4족 구름다리 1)

draft 5편(날개형 검수 지적 남은 4 · 4족 구름다리 위 걷기 1)을 틀린 쪽만 고치기 → 자동 검사 → Opus 재검수로 고쳐 2편이 통과했다
(2026-11-10 사용자 「draft 5편 고쳐」). 씨앗 app/seed/form_stories.jsonl.gz 에 넣고 다시 맞춘다 (바뀐 줄만 바뀐다).
남은 날개형 3편은 글에 엄마가 나오는데 인물 목록에 없어 draft 그대로 (그 조합은 그때 AI 가 쓴다).

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-11-10 17:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FIXED = [
    ("winged", "학교", "넘어졌어요", "천천히 다시 걸었어요", "웃음이 났어요"),
    ("quadruped", "놀이터", "친구가 울고 있었어요", "손을 흔들어 웃게 했어요", "모두 기뻐했어요"),
]


def upgrade() -> None:
    from app.seed import form_stories

    print("form card stories seed:", form_stories.apply(op.get_bind()))


def downgrade() -> None:
    # 고친 대본 대신 draft 로만 돌린다 (고치기 전 대본은 이 씨앗에 없다)
    conn = op.get_bind()
    for key in FIXED:
        conn.execute(sa.text("UPDATE card_stories SET status = 'draft' WHERE form = :f AND place = :p AND problem = :pr "
                             "AND action = :a AND result = :r AND length = 'long' AND voice_mode = 'narration'"),
                     dict(zip(("f", "p", "pr", "a", "r"), key)))
