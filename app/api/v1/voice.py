"""보호자 목소리로 나레이션 (2026-11-02, 보호자 모드).

- 로그인한 보호자만 쓴다. 아이는 계정이 없다.
- 녹음은 **저장하지 않는다.** 받은 그대로 ElevenLabs(미국)로 넘겨 목소리를 만들고 id 만 둔다.
- 동의 세 가지가 모두 있어야 받는다 — 수집 · 이용, 국외 이전, 본인 목소리(아이 · 남의 목소리 아님).
  안내문은 프론트 동의 화면(`consent_version` 판)에 있다.
- 지우면 ElevenLabs 목소리와 그 목소리로 만든 이야기 음성 파일을 함께 지운다.
  동의 기록(언제 동의 · 철회)은 남긴다.

아이 목소리 (`/me/child-voice`, 2026-11-09 교수님 지시 · 시연용): 보호자가 동의하고 아이 녹음으로 같은 방식의 목소리를 만들어
나레이션을 아이 목소리로 읽는다. 엔진은 나중에 바꾼다 — **ElevenLabs 개인정보 정책은 만 18세 미만 음성 데이터를 받지 않으므로**
지금은 어른이 기능을 시험할 때만 쓴다 (팀 결정). 녹음은 보호자 목소리처럼 저장하지 않는다.
"""

import logging

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.auth import current_user
from app.core.errors import AppError
from app.db.session import get_db
from app.models import GuardianVoice, Story, User
from app.models.mixins import utcnow
from app.schemas.common import ErrorResponse
from app.schemas.voice import GuardianVoiceResponse
from app.services import tts
from app.services.storage import get_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/me/voice", tags=["voice"])
child_router = APIRouter(prefix="/me/child-voice", tags=["voice"])
GUARDIAN, CHILD = "guardian", "child"

_ERRORS = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
# 1~3분 녹음. 프론트는 24kHz 모노 WAV 로 보내서 3분이면 약 9MB
MAX_VOICE_MB = 20
_AUDIO_TYPES = {"audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp4", "audio/webm"}


def _row(db: Session, user: User, kind: str = GUARDIAN) -> GuardianVoice | None:
    return db.scalar(
        select(GuardianVoice).where(GuardianVoice.user_id == user.id, GuardianVoice.kind == kind)
    )


def _response(row: GuardianVoice | None) -> GuardianVoiceResponse:
    active = row is not None and row.elevenlabs_voice_id is not None
    return GuardianVoiceResponse(
        registered=active,
        available=tts.voice_cloning_ready(),
        consent_version=row.consent_version if active else None,
        consented_at=row.consented_at if active else None,
    )


def _forget(db: Session, voice_id: str) -> None:
    """ElevenLabs 목소리와, 그 목소리로 만든 이야기 음성 파일을 지운다."""

    try:
        tts.delete_voice(voice_id)
    except Exception:
        # 우리 쪽은 계속 지운다. 남은 목소리는 로그로 찾아 손으로 지운다
        log.exception("ElevenLabs 목소리 %s 를 지우지 못했습니다", voice_id)
    storage = get_storage()
    for story in db.scalars(select(Story).where(Story.narrator_voice_id == voice_id)):
        storage.delete(story.audio_blob_path)
        story.audio_blob_path = None
        if story.book:
            pages = []
            for page in story.book.get("pages", []):
                storage.delete(page.get("audio_blob_path"))
                pages.append({**page, "audio_blob_path": None})
            story.book = {**story.book, "pages": pages}
        story.narrator_voice_id = None


@router.get(
    "", response_model=GuardianVoiceResponse, responses=_ERRORS, summary="보호자 목소리 상태"
)
def get_voice(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> GuardianVoiceResponse:
    return _response(_row(db, user))


@router.post(
    "",
    response_model=GuardianVoiceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="보호자 목소리 등록 (녹음 → ElevenLabs, 녹음은 저장하지 않음)",
)
def register_voice(
    request: Request,
    audio: UploadFile = File(description="보호자가 1~3분 읽은 녹음 (WAV 권장)"),
    agree_collect: bool = Form(description="[필수] 목소리 수집 · 이용 동의"),
    agree_transfer: bool = Form(description="[필수] ElevenLabs(미국) 국외 이전 동의"),
    own_voice: bool = Form(
        description="[필수] 보호자 본인 목소리이며 아이 · 다른 사람 목소리가 아님"
    ),
    consent_version: str = Form(max_length=20, description="동의한 안내문 판"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> GuardianVoiceResponse:
    if not (agree_collect and agree_transfer and own_voice):
        raise AppError("CONSENT_REQUIRED", "필수 항목에 모두 동의해야 목소리를 등록할 수 있어요.")
    return _register(request, audio, consent_version, db, user, GUARDIAN)


def _register(
    request: Request, audio: UploadFile, consent_version: str, db: Session, user: User, kind: str
) -> GuardianVoiceResponse:
    if not tts.voice_cloning_ready():
        raise AppError("VOICE_UNAVAILABLE", "지금은 목소리를 등록할 수 없어요.", 503)
    rate_limit.check("voice", request, user)
    content_type = (audio.content_type or "").split(";")[0]
    if content_type not in _AUDIO_TYPES:
        raise AppError("INVALID_AUDIO", "녹음 파일을 읽을 수 없어요.")
    max_bytes = MAX_VOICE_MB * 1024 * 1024
    data = audio.file.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise AppError("INVALID_AUDIO", f"녹음은 {MAX_VOICE_MB}MB 이하만 올릴 수 있어요.")

    row = _row(db, user, kind)
    try:
        # 이름에 사람 정보를 넣지 않는다 — 계정 id 앞자리만
        voice_id = tts.add_voice(
            f"{kind}-{str(user.id)[:8]}", data, audio.filename or "voice.wav", content_type
        )
    except Exception as e:
        log.exception("%s 목소리 등록 실패", kind)
        raise AppError(
            "VOICE_FAILED", "목소리를 만들지 못했어요. 조용한 곳에서 다시 녹음해 주세요.", 502
        ) from e
    finally:
        del data  # 녹음은 어디에도 두지 않는다

    if row is not None and row.elevenlabs_voice_id:
        _forget(db, row.elevenlabs_voice_id)  # 다시 녹음하면 예전 목소리는 지운다
    if row is None:
        row = GuardianVoice(
            user_id=user.id, kind=kind, consent_version=consent_version, consented_at=utcnow()
        )
        db.add(row)
    row.elevenlabs_voice_id = voice_id
    row.consent_version = consent_version
    row.consented_at = utcnow()
    row.withdrawn_at = None
    db.commit()
    return _response(row)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="보호자 목소리 지우기 (동의 철회) — 그 목소리로 만든 음성도 지움",
)
def delete_voice(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Response:
    return _delete(db, user, GUARDIAN)


def _delete(db: Session, user: User, kind: str) -> Response:
    row = _row(db, user, kind)
    if row is not None and row.elevenlabs_voice_id:
        _forget(db, row.elevenlabs_voice_id)
        row.elevenlabs_voice_id = None
        row.withdrawn_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── 아이 목소리 (2026-11-09, 시연용) ───


@child_router.get(
    "", response_model=GuardianVoiceResponse, responses=_ERRORS, summary="아이 목소리 상태"
)
def get_child_voice(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> GuardianVoiceResponse:
    return _response(_row(db, user, CHILD))


@child_router.post(
    "",
    response_model=GuardianVoiceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="아이 목소리 등록 (보호자 동의 · 녹음 → ElevenLabs, 녹음은 저장하지 않음, 시연용)",
)
def register_child_voice(
    request: Request,
    audio: UploadFile = File(description="아이가 30초~3분 읽은 녹음 (WAV 권장)"),
    agree_collect: bool = Form(description="[필수] 아이 목소리 수집 · 이용 동의"),
    agree_transfer: bool = Form(description="[필수] ElevenLabs(미국) 국외 이전 동의"),
    guardian_consent: bool = Form(description="[필수] 법정대리인(보호자)으로서 아이 녹음에 동의"),
    consent_version: str = Form(max_length=20, description="동의한 안내문 판"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> GuardianVoiceResponse:
    if not (agree_collect and agree_transfer and guardian_consent):
        raise AppError("CONSENT_REQUIRED", "필수 항목에 모두 동의해야 목소리를 등록할 수 있어요.")
    return _register(request, audio, consent_version, db, user, CHILD)


@child_router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="아이 목소리 지우기 (동의 철회) — 그 목소리로 만든 음성도 지움",
)
def delete_child_voice(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Response:
    return _delete(db, user, CHILD)
