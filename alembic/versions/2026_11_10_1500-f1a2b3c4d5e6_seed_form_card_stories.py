"""seed form card stories (형태별 카드 이야기 씨앗: 날개형 512 · 4족 · 사슬형 고친 것)

운영 DB 는 밖에서 쓸 수 없어 내용도 마이그레이션으로 넣는다 (팀 규칙: 구조 · 내용 변경은 마이그레이션 파일로만).
씨앗은 app/seed/form_stories.jsonl.gz (app/seed/form_stories.py 가 만든다). 같은 조합 · 형태가 있으면 씨앗 내용으로 맞추고
없으면 새로 넣는다. 몇 번 돌려도 같다.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-11-10 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.seed import form_stories

    print("form card stories seed:", form_stories.apply(op.get_bind()))


def downgrade() -> None:
    # 새로 넣은 날개형만 지운다. 4족 · 사슬형은 고치기 전 대본을 씨앗에 두지 않아 되돌리지 못한다 (고친 그대로 둔다)
    op.execute("DELETE FROM card_stories WHERE form = 'winged' AND prompt_version = 'form-2026-11-10'")
