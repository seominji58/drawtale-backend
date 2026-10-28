"""add card_stories

미리 만든 카드 이야기 (pt_docs 13 1-1, 09 8장 안). 조합 × 길이 × 읽어주기마다 하나, 사람이 읽고 통과시킨 것만 꺼낸다.

Revision ID: a9b0c1d2e3f4
Revises: f8a1b2c3d4e5
Create Date: 2026-10-28 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "f8a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "card_stories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("place", sa.String(length=50), nullable=False),
        sa.Column("problem", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("result", sa.String(length=50), nullable=False),
        sa.Column("length", sa.String(length=10), nullable=False),
        sa.Column("voice_mode", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("reviewed_by", sa.String(length=60), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("place", "problem", "action", "result", "length", "voice_mode", name="uq_card_story"),
    )
    op.create_index(op.f("ix_card_stories_status"), "card_stories", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_card_stories_status"), table_name="card_stories")
    op.drop_table("card_stories")
