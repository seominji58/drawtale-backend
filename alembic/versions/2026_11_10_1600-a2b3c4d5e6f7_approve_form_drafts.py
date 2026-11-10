"""approve 12 form card story drafts (예전 손 검사로 draft 였던 4족 · 사슬형 13편을 읽어 봄)

운영 DB 의 4족 · 사슬형 draft 13편은 예전 손 검사(scripts/batch_form_stories.HERO_HAND)가 「손」 글자에 걸린 것이다
(「손을 흔들듯 몸을 흔들어」처럼 이미 몸으로 바꿔 쓴 대본). 2026-11-10 사용자 「13편 읽어 보고 괜찮으면 approved 로 바꿔」:
12편은 지금 몸 규칙 · 검사를 지나고 읽어 봐도 괜찮아 approved. 1편(4족 · 놀이터 · 모두 기뻐했어요)은 「구름다리 위를 걸었어요」로
꼭대기를 걷는 위험한 장면이라 draft 로 두고 메모만 바꾼다. 운영 DB 내용 변경은 마이그레이션으로만 (팀 규칙).

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-11-10 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEY = ("form", "place", "problem", "action", "result")
APPROVE = [
    ('chain', '숲', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '모두 기뻐했어요'),
    ('chain', '마을', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친한 친구가 됐어요'),
    ('chain', '마을', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친구 눈물이 그쳤어요'),
    ('chain', '바다', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친구 눈물이 그쳤어요'),
    ('chain', '우주', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친구 눈물이 그쳤어요'),
    ('chain', '학교', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '고맙다는 말을 들었어요'),
    ('chain', '학교', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친한 친구가 됐어요'),
    ('chain', '학교', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친구 눈물이 그쳤어요'),
    ('chain', '놀이터', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친구 눈물이 그쳤어요'),
    ('quadruped', '숲', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '친구 눈물이 그쳤어요'),
    ('quadruped', '집', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '모두 기뻐했어요'),
    ('quadruped', '마을', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '모두 기뻐했어요'),
]
KEEP = ('quadruped', '놀이터', '친구가 울고 있었어요', '손을 흔들어 웃게 했어요', '모두 기뻐했어요')
OLD_NOTE = "주인공이 손 · 팔을 씀 " + chr(0x2014) + " 이 형태에 맞지 않음"   # 예전 메모 그대로 (되돌릴 때)
UNSAFE = "위험: 구름다리 위를 걷는 장면 (1 · 8쪽), 꼭대기에 올라가 걷기는 따라 하면 다친다 (11/10 읽어 봄)"


def _where(key):
    return " AND ".join(f"{k} = :{k}" for k in KEY), dict(zip(KEY, key))


def upgrade() -> None:
    conn = op.get_bind()
    for key in APPROVE:
        cond, params = _where(key)
        conn.execute(sa.text(f"UPDATE card_stories SET status = 'approved', reviewed_by = 'read 2026-11-10', note = NULL "
                             f"WHERE {cond} AND length = 'long' AND voice_mode = 'narration' AND status = 'draft'"), params)
    cond, params = _where(KEEP)
    conn.execute(sa.text(f"UPDATE card_stories SET note = :note WHERE {cond} AND length = 'long' AND voice_mode = 'narration'"),
                 {**params, "note": UNSAFE})


def downgrade() -> None:
    conn = op.get_bind()
    for key in [*APPROVE, KEEP]:
        cond, params = _where(key)
        conn.execute(sa.text(f"UPDATE card_stories SET status = 'draft', reviewed_by = NULL, note = :note "
                             f"WHERE {cond} AND length = 'long' AND voice_mode = 'narration'"), {**params, "note": OLD_NOTE})
