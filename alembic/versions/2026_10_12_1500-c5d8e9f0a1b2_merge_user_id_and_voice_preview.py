"""merge characters.user_id (e1f2a3b4c5d6) and voice · preview (b4c7d8e9f0a1)

두 갈래가 같은 d9e3f4a5b6c7 에서 나왔다. 하나는 그림을 계정에 묶는 user_id(2026-10-12),
하나는 읽어주기 두 판 · 인물 목소리 · 미리보기 영상(2026-10-08, 늦게 push 됨).
서로 다른 열만 더하므로 바꾸는 것 없이 이어 주기만 한다. 어느 쪽이 먼저 올라가 있어도 upgrade head 가 남은 쪽을 올린다.

Revision ID: c5d8e9f0a1b2
Revises: e1f2a3b4c5d6, b4c7d8e9f0a1
Create Date: 2026-10-12 15:00:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'c5d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = ('e1f2a3b4c5d6', 'b4c7d8e9f0a1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
