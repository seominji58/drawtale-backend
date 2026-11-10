"""add motion_clips to characters

긴 이야기 쪽 동작 영상(통통 뛰기)을 캐릭터를 만든 직후 미리 렌더해 둔다.
이야기를 만들 때 렌더하면 한 편이 2~5분 걸렸다 (2026-10-12 실측).

Revision ID: e7f0a1b2c3d4
Revises: d6e9f0a1b2c3
Create Date: 2026-10-12 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f0a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'd6e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('characters', sa.Column('motion_clips', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('characters', 'motion_clips')
