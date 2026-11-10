"""add form to card_stories (형태별 미리 만든 이야기)

비휴머노이드(4족형 · 사슬형 …)도 카드 이야기를 미리 써 둔다 (2026-11-09 사용자 — 대본 쓰는 시간이 길다).
예전 줄은 모두 사람형(biped). 같은 조합이라도 형태마다 하나.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-11-09 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("card_stories", sa.Column("form", sa.String(length=20), nullable=False, server_default="biped"))
    op.drop_constraint("uq_card_story", "card_stories", type_="unique")
    op.create_unique_constraint(
        "uq_card_story", "card_stories", ["place", "problem", "action", "result", "length", "voice_mode", "form"])


def downgrade() -> None:
    op.execute("DELETE FROM card_stories WHERE form <> 'biped'")
    op.drop_constraint("uq_card_story", "card_stories", type_="unique")
    op.create_unique_constraint(
        "uq_card_story", "card_stories", ["place", "problem", "action", "result", "length", "voice_mode"])
    op.drop_column("card_stories", "form")
