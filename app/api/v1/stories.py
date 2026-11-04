import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.core import rate_limit
from app.core.auth import current_user
from app.core.authz import owned_character, owned_story
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Character, GuardianVoice, Job, Story, StoryActivity, User
from app.models.mixins import utcnow
from app.schemas.common import ErrorResponse, JobStatus, JobType
from app.schemas.story import (
    ActivityCreateRequest,
    ActivityResponse,
    Narrator,
    StoryCreateRequest,
    StoryCreateResponse,
    StoryResponse,
    StoryUpdateRequest,
)
from app.services import forms
from app.services.jobs import (
    MOTION_ACTION, cancel_job, current_joints, facing_of, motion_for, read_sprite_meta, read_still_hands, run_story_job, sprite_path_for,
)
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
            # 바탕이 투명한 프레임 묶음 (S-09 장면 층, 2026-11-03).
            # 정사각 칸 · 가로 8칸씩. 없으면 null
            "sprite_url": storage.url(p.get("sprite_blob_path")),
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
    user: User = Depends(current_user),
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


@router.post(
    "/{story_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": ErrorResponse}},
    summary="이야기 만들기 그만두기 (S-08 「그만하기」)",
)
def cancel_story(
    story_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """돌고 있는 작업에 그만두라고 알린다. 작업은 지금 단계를 마치는 대로 멈추고(CANCELLED) 같은 계정의
    「작업 중」 자리를 비운다. 이미 끝난 이야기는 그대로 둔다 (2026-11-04 — 전에는 그만둬도 끝까지 돌아
    그동안 새 이야기가 BUSY 였다)."""

    story = owned_story(db, story_id, user)
    job = db.scalar(select(Job).where(Job.story_id == story.id).order_by(Job.created_at.desc()))
    if job is not None and job.status in (JobStatus.pending, JobStatus.running):
        cancel_job(job.id)
    return {"story_id": str(story.id), "status": job.status if job else None}


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
    user: User = Depends(current_user),
) -> StoryResponse:
    return story_response(owned_story(db, story_id, user))


@router.patch(
    "/{story_id}",
    response_model=StoryResponse,
    responses={404: {"model": ErrorResponse}},
    summary="책 제목 바꾸기 (S-11 「다 했어요」 · S-12 책장 · S-13)",
)
def update_story(
    story_id: uuid.UUID,
    body: StoryUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> StoryResponse:
    story = owned_story(db, story_id, user)
    story.title = body.title
    db.commit()
    db.refresh(story)
    return story_response(story)


def story_response(story: Story) -> StoryResponse:
    storage = get_storage()
    # 짧은 이야기의 동작 영상 옆 투명 프레임 묶음 (2026-11-03) — 화면이 긴 이야기처럼 장면 층으로 쌓는다
    clip = story.animation_blob_path if (story.animation_blob_path or "").endswith(".mp4") else None
    meta = read_sprite_meta(storage, clip) if clip and not story.book else None
    # 주인공 형태 · 머리 쪽 (11/4 운영 — 화면은 머리 쪽을 세션 관절로만 알아서, 새로고침 · 책장에서 연 이야기는
    # 옆모습 주인공이 머리 반대쪽으로 갈 때 뒤집지 못해 뒷걸음질했다)
    character = object_session(story).get(Character, story.character_id)
    hero_form = character.form if character else None
    side = hero_form is not None and (forms.get(hero_form).get("hero") or {}).get("facing") == "auto"
    hero_facing = facing_of(current_joints(character)) if side else None
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
        title=story.title,
        length=story.length,
        narrator=Narrator.guardian if story.narrator_voice_id else Narrator.default,
        # 정한 제목이 있으면 책 제목도 그것으로 (책장 · 쪽 머리가 같은 제목)
        book=book_response({**story.book, "title": story.title} if story.book and story.title else story.book, storage),
        text=story.text,
        audio_url=storage.url(story.audio_blob_path),
        animation_url=storage.url(story.animation_blob_path),
        still_url=storage.url(story.still_blob_path),
        still_hands=read_still_hands(storage, story.still_blob_path),
        hero_form=hero_form,
        hero_facing=hero_facing,
        sprite_url=storage.url(sprite_path_for(clip)) if meta else None,
        sprite_frames=meta["frames"] if meta else None,
        sprite_box=meta["box"] if meta else None,
        action_motion=MOTION_ACTION.get(motion_for(story.action), "wave"),
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
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
) -> list[ActivityResponse]:
    story = owned_story(db, story_id, user)
    rows = db.scalars(
        select(StoryActivity)
        .where(StoryActivity.story_id == story.id)
        .order_by(StoryActivity.created_at)
    )
    return [ActivityResponse.model_validate(r, from_attributes=True) for r in rows]
