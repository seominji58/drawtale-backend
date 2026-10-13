"""add protagonist_name to stories

Revision ID: a3b6c7d8e9f0
Revises: f2a5b6c7d8e9
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b6c7d8e9f0'
down_revision: Union[str, Sequence[str], None] = 'f2a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 아이가 지은 주인공 이름. 캐릭터 목소리 판 나레이션이 3인칭으로 부른다
    op.add_column('stories', sa.Column('protagonist_name', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stories', 'protagonist_name')
