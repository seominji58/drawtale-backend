"""add story still

이야기마다 배경을 잘라 낸 캐릭터 정지 그림 사본 (2026-11-03). 영상이 없는 쪽 · 완료 화면 · 표지에 쓴다.
캐릭터 쪽 사본은 원본을 지울 때 같이 지워져, 영상처럼 이야기 폴더에 따로 둔다.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-11-03 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("stories", sa.Column("still_blob_path", sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stories", "still_blob_path")
