"""add guardian voices

보호자 목소리로 나레이션 (2026-11-02). 녹음은 저장하지 않고 ElevenLabs 목소리 id 와 동의 기록만 둔다.
이야기마다 어느 목소리로 읽었는지(narrator_voice_id) — 목소리를 지우면 그 음성 파일도 지운다.

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-11-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "guardian_voices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("elevenlabs_voice_id", sa.String(length=64), nullable=True),
        sa.Column("consent_version", sa.String(length=20), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_guardian_voices_user_id"), "guardian_voices", ["user_id"], unique=True)
    op.add_column("stories", sa.Column("narrator_voice_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stories", "narrator_voice_id")
    op.drop_index(op.f("ix_guardian_voices_user_id"), table_name="guardian_voices")
    op.drop_table("guardian_voices")
