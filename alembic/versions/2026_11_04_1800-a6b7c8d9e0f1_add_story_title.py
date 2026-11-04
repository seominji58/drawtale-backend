"""add story title

아이 · 보호자가 정한 책 제목 (S-11 「다 했어요」 · S-12 책장 · S-13, 2026-11-04).
전에는 고친 제목이 기기(localStorage)에만 있어 다른 기기에서는 처음 제목이었다. 예전 이야기는 null.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-11-04 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("stories", sa.Column("title", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stories", "title")
