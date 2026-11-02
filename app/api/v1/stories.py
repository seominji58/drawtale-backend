import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.auth import optional_user
from app.core.authz import owned_character, owned_story
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models import GuardianVoice, Job, Story, StoryActivity, User
from app.models.mixins import utcnow
from app.schemas.common import ErrorResponse, JobStatus, JobType
from app.schemas.story import (
    ActivityCreateRequest,
    ActivityResponse,
    Narrator,
    StoryCreateRequest,
    StoryCreateResponse,
    StoryResponse,
)
from app.services.jobs import run_story_job
from app.services.storage import get_storage

router = APIRouter(prefix="/stories", tags=["stories"])

_ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}


def book_response(book: dict | None, storage) -> dict | None:
    """저장된 대본의 쪽 파일 경로를 주소로 바꾼다 (계약 2-6)."""

    if not book:
        return None
    pages = [
        {
            **{k: v for k, v in p.items() if not k.endswith("_blob_path")},
            "audio_url": storage.url(p.get("audio_blob_path")),
            "animation_url": storage.url(p.get("animation_blob_path")),
        }
        for p in book["pages"]
    ]
    return {**book, "pages": pages}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StoryCreateResponse,
    responses=_ERRORS,
    summary="장소-문제-행동-결과 기반 이야기 생성 Job",
)
def create_story(
    request: Request,
    body: StoryCreateRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> StoryCreateResponse:
    character = owned_character(db, body.character_id, user)
    if character.status != JobStatus.succeeded:
        raise AppError("CHARACTER_NOT_READY", "그림 분석이 끝난 뒤에 이야기를 만들 수 있어요.", 409)

    rate_limit.check("story", request, user)
    _daily_limits(db, request, user)
    # 비회원은 교실에서 IP 를 함께 쓰므로 그림 하나에 작업 하나로 막는다
    busy_key = f"story:{user.id if user else character.id}"
    rate_limit.claim(busy_key)
    try:
        story = Story(**body.model_dump(exclude={"narrator"}))
        if body.narrator == Narrator.guardian and user is not None:
            voice = db.scalar(select(GuardianVoice).where(GuardianVoice.user_id == user.id))
            story.narrator_voice_id = voice.elevenlabs_voice_id if voice else None
        db.add(story)
        db.flush()
        job = Job(type=JobType.story, character_id=character.id, story_id=story.id)
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        rate_limit.release(busy_key)
        raise

    background.add_task(rate_limit.run_then_release, busy_key, run_story_job, job.id)
    return StoryCreateResponse(story_id=story.id, job_id=job.id, status=job.status)


def _daily_limits(db: Session, request: Request, user: User | None) -> None:
    """이야기 하루 한도 (2026-11-02 보안).

    이야기 하나가 Claude · ElevenLabs · OpenAI 검열을 부를 수 있어서 비회원은 IP 마다,
    회원은 계정마다(429), 서버 전체는 DB 로(503) 센다
    """

    s = get_settings()
    per_day = s.guest_stories_per_day if user is None else s.member_stories_per_day
    rate_limit.daily("story", request, user, per_day)
    if s.max_stories_per_day > 0:
        since = utcnow() - timedelta(days=1)
        made = db.scalar(
            select(func.count()).select_from(Story).where(Story.created_at >= since)
        ) or 0
        if made >= s.max_stories_per_day:
            raise AppError(
                "DAILY_LIMIT", "오늘은 이야기를 더 만들 수 없어요. 내일 다시 해 주세요.", 503
            )


@router.get(
    "/{story_id}",
    response_model=StoryResponse,
    responses={404: {"model": ErrorResponse}},
    summary="이야기 조회 (텍스트, 음성, 애니메이션)",
)
def get_story(
    story_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> StoryResponse:
    story = owned_story(db, story_id, user)
    storage = get_storage()
    return StoryResponse(
        id=story.id,
        character_id=story.character_id,
        status=story.status,
        place=story.place,
        problem=story.problem,
        action=story.action,
        result=story.result,
        voice_mode=story.voice_mode,
        protagonist_voice=story.protagonist_voice,
        protagonist_name=story.protagonist_name,
        length=story.length,
        narrator=Narrator.guardian if story.narrator_voice_id else Narrator.default,
        book=book_response(story.book, storage),
        text=story.text,
        audio_url=storage.url(story.audio_blob_path),
        animation_url=storage.url(story.animation_blob_path),
        created_at=story.created_at,
        updated_at=story.updated_at,
    )


@router.post(
    "/{story_id}/activity",
    status_code=status.HTTP_201_CREATED,
    response_model=ActivityResponse,
    responses={404: {"model": ErrorResponse}},
    summary="S-10 순서 맞추기 기록 (시도 횟수, 완료 여부)",
)
def create_activity(
    story_id: uuid.UUID,
    body: ActivityCreateRequest,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> ActivityResponse:
    story = owned_story(db, story_id, user)
    activity = StoryActivity(story_id=story.id, **body.model_dump())
    db.add(activity)
    db.commit()
    return ActivityResponse.model_validate(activity, from_attributes=True)


@router.get(
    "/{story_id}/activity",
    response_model=list[ActivityResponse],
    responses={404: {"model": ErrorResponse}},
    summary="이 이야기의 순서 맞추기 기록 (오래된 것부터)",
)
def list_activity(
    story_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> list[ActivityResponse]:
    story = owned_story(db, story_id, user)
    rows = db.scalars(
        select(StoryActivity)
        .where(StoryActivity.story_id == story.id)
        .order_by(StoryActivity.created_at)
    )
    return [ActivityResponse.model_validate(r, from_attributes=True) for r in rows]
