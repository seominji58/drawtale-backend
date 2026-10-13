"""add keep_original and original_deleted_at to characters

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-10-02 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b7c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 이미 있는 캐릭터는 보관으로 둔다. 예전 업로드를 이 migration 이 지우지 않게 한다
    op.add_column(
        'characters',
        sa.Column('keep_original', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column('characters', 'keep_original', server_default=sa.false())
    op.add_column(
        'characters', sa.Column('original_deleted_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('characters', 'original_deleted_at')
    op.drop_column('characters', 'keep_original')
