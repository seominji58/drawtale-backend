"""add preview_blob_path to characters

Revision ID: b4c7d8e9f0a1
Revises: a3b6c7d8e9f0
Create Date: 2026-10-08 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c7d8e9f0a1'
down_revision: Union[str, Sequence[str], None] = 'a3b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # S-05 미리보기 MP4 (Meta 렌더)
    op.add_column('characters', sa.Column('preview_blob_path', sa.String(length=500), nullable=True))
    # rendering | ready | failed
    op.add_column('characters', sa.Column('preview_status', sa.String(length=10), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('characters', 'preview_status')
    op.drop_column('characters', 'preview_blob_path')
