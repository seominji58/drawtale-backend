import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import get_db
from app.models import Character, Job, Story
from app.schemas.common import ErrorResponse, JobStatus, JobType
from app.schemas.story import StoryCreateRequest, StoryCreateResponse, StoryResponse
from app.services.jobs import run_story_job
from app.services.storage import get_storage

router = APIRouter(prefix="/stories", tags=["stories"])

_ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StoryCreateResponse,
    responses=_ERRORS,
    summary="장소-문제-행동-결과 기반 이야기 생성 Job",
)
def create_story(
    body: StoryCreateRequest, background: BackgroundTasks, db: Session = Depends(get_db)
) -> StoryCreateResponse:
    character = db.get(Character, body.character_id)
    if character is None:
        raise AppError("CHARACTER_NOT_FOUND", "캐릭터를 찾을 수 없어요.", 404)
    if character.status != JobStatus.succeeded:
        raise AppError("CHARACTER_NOT_READY", "그림 분석이 끝난 뒤에 이야기를 만들 수 있어요.", 409)

    story = Story(**body.model_dump())
    db.add(story)
    db.flush()
    job = Job(type=JobType.story, character_id=character.id, story_id=story.id)
    db.add(job)
    db.commit()

    background.add_task(run_story_job, job.id)
    return StoryCreateResponse(story_id=story.id, job_id=job.id, status=job.status)


@router.get(
    "/{story_id}",
    response_model=StoryResponse,
    responses={404: {"model": ErrorResponse}},
    summary="이야기 조회 (텍스트, 음성, 애니메이션)",
)
def get_story(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryResponse:
    story = db.get(Story, story_id)
    if story is None:
        raise AppError("STORY_NOT_FOUND", "이야기를 찾을 수 없어요.", 404)
    storage = get_storage()
    return StoryResponse(
        id=story.id,
        character_id=story.character_id,
        status=story.status,
        place=story.place,
        problem=story.problem,
        action=story.action,
        result=story.result,
        text=story.text,
        audio_url=storage.url(story.audio_blob_path),
        animation_url=storage.url(story.animation_blob_path),
        created_at=story.created_at,
        updated_at=story.updated_at,
    )
