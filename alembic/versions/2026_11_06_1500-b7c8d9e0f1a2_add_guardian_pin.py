"""add guardian pin and onboarded_at

보호자 비밀번호(숫자 4자리, 해시만) · 연달아 틀린 횟수 · 잠금 시각, 보호자 소개 카드를 본 때
(pt_docs 20 3장 「기능 드러내기」, 2026-11-06). 예전 계정은 비밀번호 없음 —
보호자 칸을 처음 열 때 만든다.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-11-06 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("guardian_pin_hash", sa.String(length=160), nullable=True))
    op.add_column(
        "users",
        sa.Column("guardian_pin_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users", sa.Column("guardian_pin_locked_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "onboarded_at")
    op.drop_column("users", "guardian_pin_locked_until")
    op.drop_column("users", "guardian_pin_failures")
    op.drop_column("users", "guardian_pin_hash")
