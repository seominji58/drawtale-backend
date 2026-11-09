"""add age verification to users

보호자 나이 확인(포트원 V2 + KG이니시스 본인인증, 2026-11-09). 확인한 때 · 방법 이름 · 포트원 인증 건 번호만.
예전 계정은 비어 있다, 다음 로그인 때 한 번 인증한다.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-11-09 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("age_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users", sa.Column("age_verification_method", sa.String(length=40), nullable=True)
    )
    op.add_column("users", sa.Column("age_verification_id", sa.String(length=100), nullable=True))
    op.create_unique_constraint("uq_users_age_verification_id", "users", ["age_verification_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_users_age_verification_id", "users", type_="unique")
    op.drop_column("users", "age_verification_id")
    op.drop_column("users", "age_verification_method")
    op.drop_column("users", "age_verified_at")
