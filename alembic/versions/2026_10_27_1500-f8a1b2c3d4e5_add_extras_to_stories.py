"""add extras to stories

S-07 네 칸 사이에 더 받는 칸(함께 나오는 인물 · 문제 때 기분 · 마지막 기분).
2026-10-27 로컬 시험 — 회의에서 빼면 downgrade 하고 이 파일을 지운다 (프론트 open-decisions 0-11).

Revision ID: f8a1b2c3d4e5
Revises: e7f0a1b2c3d4
Create Date: 2026-10-27 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e7f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("stories", sa.Column("extras", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stories", "extras")
