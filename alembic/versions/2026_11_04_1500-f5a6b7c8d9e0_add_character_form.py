"""add character form

캐릭터의 형태(그림의 몸 구조 — 2족형 · 4족형 …, pt_docs 19)와, AI 가 관절을 실제로 찾았는지 (2026-11-04).
형태마다 관절 이름이 다르다. 예전 캐릭터는 모두 사람(biped). joints_found 가 거짓이면 형태의 기본 뼈대라
화면이 관절 고치기로 보낸다 — 예전 캐릭터는 null(찾은 것으로 본다).

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-11-04 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("characters", sa.Column("form", sa.String(length=20), nullable=False, server_default="biped"))
    op.add_column("characters", sa.Column("joints_found", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "joints_found")
    op.drop_column("characters", "form")
