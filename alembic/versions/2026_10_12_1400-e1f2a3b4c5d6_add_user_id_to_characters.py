"""add user_id to characters

로그인한 사람이 올린 그림을 그 계정에 묶는다. 로그인하지 않고도 쓸 수 있으므로
(F-ACC-01) 비어 있을 수 있다. 이미 있는 그림은 전부 주인 없는 것으로 남는다.

Revision ID: e1f2a3b4c5d6
Revises: d9e3f4a5b6c7
Create Date: 2026-10-12 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd9e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('characters', sa.Column('user_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_characters_user_id'), 'characters', ['user_id'])
    op.create_foreign_key(
        'fk_characters_user_id_users',
        'characters',
        'users',
        ['user_id'],
        ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_characters_user_id_users', 'characters', type_='foreignkey')
    op.drop_index(op.f('ix_characters_user_id'), table_name='characters')
    op.drop_column('characters', 'user_id')
