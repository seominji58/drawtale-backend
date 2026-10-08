"""add voice_mode to stories

Revision ID: e1f4a5b6c7d8
Revises: d9e3f4a5b6c7
Create Date: 2026-10-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd9e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # S-13 「읽어주기」. 이미 있는 이야기는 나레이션 판이다
    op.add_column(
        'stories',
        sa.Column('voice_mode', sa.String(length=20), nullable=False, server_default='narration'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stories', 'voice_mode')
