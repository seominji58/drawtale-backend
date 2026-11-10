"""add kind to guardian_voices (guardian · child)

아이 목소리로 이야기 듣기 (2026-11-09, 교수님 지시 · 시연용). 계정마다 하나였던 목소리를 종류(보호자 · 아이)별 하나로.
예전 줄은 모두 guardian.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-11-09 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "guardian_voices",
        sa.Column("kind", sa.String(length=10), nullable=False, server_default="guardian"),
    )
    op.drop_index(op.f("ix_guardian_voices_user_id"), table_name="guardian_voices")
    op.create_index(op.f("ix_guardian_voices_user_id"), "guardian_voices", ["user_id"], unique=False)
    op.create_unique_constraint("uq_guardian_voices_user_kind", "guardian_voices", ["user_id", "kind"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_guardian_voices_user_kind", "guardian_voices", type_="unique")
    op.execute("DELETE FROM guardian_voices WHERE kind <> 'guardian'")
    op.drop_index(op.f("ix_guardian_voices_user_id"), table_name="guardian_voices")
    op.create_index(op.f("ix_guardian_voices_user_id"), "guardian_voices", ["user_id"], unique=True)
    op.drop_column("guardian_voices", "kind")
