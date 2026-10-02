"""원본 그림 지우기 (S-13 「원본 그림 보관」).

렌더링에는 원본이 필요하고, 같은 그림으로 이야기를 또 만들 수 있다 (S-11 「새 이야기 만들기」).
그래서 분석 직후가 아니라 아이가 그 그림을 떠날 때 프론트가 지우라고 한다
(`DELETE /api/v1/characters/{id}/original`). 그 요청이 오지 않아도(탭을 그냥 닫음 등)
보관하지 않는 그림은 `original_retention_hours` 가 지나면 여기서 지운다.

지우는 것: 업로드 파일, AI 서버 세션(원본 사본과 마스크).
남기는 것: 관절 좌표, 이야기 문장 · 음성 · MP4. MP4 는 그림을 움직인 결과라 이야기와 함께 둔다.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import session as db_session
from app.models import Character, Story
from app.models.mixins import utcnow
from app.services.ai_client import get_ai_client
from app.services.storage import get_storage

log = logging.getLogger(__name__)


def delete_original(db: Session, character: Character) -> None:
    """원본을 지운다. 이미 지웠으면 아무것도 하지 않는다. 커밋은 부르는 쪽이 한다."""

    if character.original_deleted_at is not None:
        return

    storage = get_storage()
    storage.delete(character.upload_blob_path)

    if character.ai_request_id:
        get_ai_client().delete_session(character.ai_request_id)
        character.ai_request_id = None

    # 목 AI 는 MP4 대신 원본 경로를 animation 에 넣는다. 지운 파일을 가리키지 않게 한다
    for story in db.scalars(
        select(Story).where(
            Story.character_id == character.id,
            Story.animation_blob_path == character.upload_blob_path,
        )
    ):
        story.animation_blob_path = None

    character.original_deleted_at = utcnow()
    log.info("deleted original of character %s", character.id)


def purge_expired_originals() -> int:
    """보관하지 않는 그림 중 보관 시간이 지난 것을 지운다. 지운 개수를 돌려준다."""

    hours = get_settings().original_retention_hours
    cutoff = utcnow() - timedelta(hours=hours)
    with db_session.SessionLocal() as db:
        expired = db.scalars(
            select(Character).where(
                Character.keep_original.is_(False),
                Character.original_deleted_at.is_(None),
                Character.created_at < cutoff,
                # 분석이나 이야기 렌더가 아직 도는 그림은 건드리지 않는다
                Character.status.in_(["succeeded", "failed"]),
            )
        ).all()
        for character in expired:
            delete_original(db, character)
        db.commit()
    return len(expired)
