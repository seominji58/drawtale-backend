"""seed finned · radial card stories (지느러미형 · 여러 다리형 20편씩)

지느러미형 · 여러 다리형(촉수형)은 scripts/batch_form_stories 로 20편씩만 만들어 storage/card_stories/form-finned.jsonl ·
form-radial.jsonl 에만 있었다 (2026-11-10, 60줄 중 radial 은 같은 조합을 두 번 만들어 나중 줄을 씀). 모두 자동 검사(11/13 다시)
· Opus 검수(통과 또는 검수 고침)를 지나 씨앗(app/seed/form_stories.jsonl.gz)에 approved 로 넣고 다시 맞춘다 (2026-11-13 사용자
「두 형태 대본도 운영에 반영해」). 날개형 · 사슬 · 4족 829편은 씨앗이 그대로라 「같음」. 없는 조합은 전처럼 그 자리에서 만든다.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-13 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.seed import form_stories

    print("form card stories seed:", form_stories.apply(op.get_bind()))


def downgrade() -> None:
    # 두 형태 저장본은 이 씨앗으로만 들어온다
    op.get_bind().execute(sa.text("DELETE FROM card_stories WHERE form IN ('finned', 'radial') AND reviewed_by = 'auto-check+opus-review'"))
