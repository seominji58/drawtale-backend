"""계정 탈퇴 — 그 계정의 것을 남김없이 지운다 (2026-11-03, 사용자 「계정 탈퇴 기능도 로그아웃 밑에」).

pt_docs 02 6장 · 09 5장 「계정을 지우면 아무것도 남지 않는다」. DB 는 모든 연결에 ON DELETE CASCADE 가
걸려 있지만, 파일(Blob) · ElevenLabs 목소리 · AI 세션은 DB 밖이라 여기서 먼저 지운다.
SQLite(테스트)는 외래 키 CASCADE 가 꺼져 있을 수 있어 표도 아래에서부터 직접 지운다.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuthToken, SocialAccount, User
from app.models.character import Character, JointCorrection
from app.models.guardian_voice import GuardianVoice
from app.models.job import Job
from app.models.story import Story, StoryActivity
from app.services.ai_client import get_ai_client
from app.services.jobs import sprite_meta_path, sprite_path_for
from app.services.storage import get_storage

log = logging.getLogger(__name__)


def _clip_files(path: str | None) -> list[str]:
    """동작 영상과 옆에 둔 투명 프레임 묶음 · 그 정보"""
    if not path:
        return []
    return [path, sprite_path_for(path), sprite_meta_path(path)] if path.endswith(".mp4") else [path]


def account_files(db: Session, user: User) -> list[str]:
    """이 계정이 가진 파일 경로 전부 (그림 · 잘라 낸 그림 · 미리보기 · 동작 · 이야기 음성 · 영상 · 정지 그림)"""
    files: list[str] = []
    characters = list(db.scalars(select(Character).where(Character.user_id == user.id)))
    for c in characters:
        files += [c.upload_blob_path, c.mask_blob_path, c.cutout_blob_path]
        files += _clip_files(c.preview_blob_path)
        for path in (c.motion_clips or {}).values():
            files += _clip_files(path)
        for s in db.scalars(select(Story).where(Story.character_id == c.id)):
            files += [s.audio_blob_path, s.still_blob_path] + _clip_files(s.animation_blob_path)
            for page in (s.book or {}).get("pages", []):
                files += [page.get("audio_blob_path"), page.get("still_blob_path")]
                files += _clip_files(page.get("animation_blob_path"))
    return list(dict.fromkeys(p for p in files if p))


def delete_account(db: Session, user: User) -> None:
    """계정과 그 아래 전부를 지운다. 바깥(파일 · 목소리 · AI 세션)은 실패해도 계속 지우고 로그로 남긴다. 커밋까지 한다."""
    from app.api.v1.voice import _forget   # 보호자 목소리 지우기와 같은 길 (그 목소리로 만든 음성까지)

    voice = db.scalar(select(GuardianVoice).where(GuardianVoice.user_id == user.id))
    if voice is not None and voice.elevenlabs_voice_id:
        _forget(db, voice.elevenlabs_voice_id)

    storage = get_storage()
    for path in account_files(db, user):
        try:
            storage.delete(path)
        except Exception:  # noqa: BLE001
            log.exception("탈퇴: 파일 %s 를 지우지 못했습니다", path)

    characters = list(db.scalars(select(Character).where(Character.user_id == user.id)))
    for c in characters:
        if c.ai_request_id:
            try:
                get_ai_client().delete_session(c.ai_request_id)
            except Exception:  # noqa: BLE001
                log.exception("탈퇴: AI 세션 %s 를 지우지 못했습니다", c.ai_request_id)

    ids = [c.id for c in characters]
    story_ids = list(db.scalars(select(Story.id).where(Story.character_id.in_(ids)))) if ids else []
    if story_ids:
        db.execute(delete(StoryActivity).where(StoryActivity.story_id.in_(story_ids)))
        db.execute(delete(Job).where(Job.story_id.in_(story_ids)))
        db.execute(delete(Story).where(Story.id.in_(story_ids)))
    if ids:
        db.execute(delete(Job).where(Job.character_id.in_(ids)))
        db.execute(delete(JointCorrection).where(JointCorrection.character_id.in_(ids)))
        db.execute(delete(Character).where(Character.id.in_(ids)))
    db.execute(delete(GuardianVoice).where(GuardianVoice.user_id == user.id))
    db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
    db.execute(delete(SocialAccount).where(SocialAccount.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    log.info("탈퇴: 계정 %s 와 캐릭터 %d · 이야기 %d 를 지웠습니다", user.id, len(ids), len(story_ids))
