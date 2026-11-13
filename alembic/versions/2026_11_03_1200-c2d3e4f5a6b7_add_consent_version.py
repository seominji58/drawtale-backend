"""add consent version

가입할 때 어느 판의 약관 · 개인정보 처리방침에 동의했는지 (2026-11-03).
가입 화면이 약관 전문을 보여 주고 항목마다 따로 동의를 받게 바뀌었다. 그 전에 가입한 계정은 비어 있다.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-03 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("consent_version", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "consent_version")
