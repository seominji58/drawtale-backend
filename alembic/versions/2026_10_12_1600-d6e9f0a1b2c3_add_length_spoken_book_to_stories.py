"""add length, spoken, book to stories

긴 이야기 (쪽 단위 그림책). 기존 이야기는 length=short 로 남는다.

Revision ID: d6e9f0a1b2c3
Revises: c5d8e9f0a1b2
Create Date: 2026-10-12 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6e9f0a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'c5d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('stories', sa.Column('length', sa.String(length=10), nullable=False, server_default='short'))
    op.add_column('stories', sa.Column('spoken', sa.JSON(), nullable=True))
    op.add_column('stories', sa.Column('book', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stories', 'book')
    op.drop_column('stories', 'spoken')
    op.drop_column('stories', 'length')
