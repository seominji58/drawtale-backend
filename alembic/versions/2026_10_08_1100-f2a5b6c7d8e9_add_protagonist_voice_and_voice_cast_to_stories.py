"""add protagonist_voice and voice_cast to stories

Revision ID: f2a5b6c7d8e9
Revises: e1f4a5b6c7d8
Create Date: 2026-10-08 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'e1f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # S-13 「주인공 목소리」와, 캐릭터 목소리 판에서 정한 인물 → 목소리
    op.add_column(
        'stories',
        sa.Column('protagonist_voice', sa.String(length=10), nullable=False, server_default='girl'),
    )
    op.add_column('stories', sa.Column('voice_cast', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stories', 'voice_cast')
    op.drop_column('stories', 'protagonist_voice')
