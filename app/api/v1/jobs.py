import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import optional_user
from app.core.authz import owned_character, owned_story
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Job, User
from app.schemas.common import ErrorInfo, ErrorResponse
from app.schemas.job import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}},
    summary="분석/이야기 Job 진행 상태 조회 (polling)",
)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> JobResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise AppError("JOB_NOT_FOUND", "작업을 찾을 수 없어요.", 404)
    # Job 은 그림 · 이야기에 딸린 것이라 주인도 그쪽을 따라간다.
    # 남의 작업이면 없는 것처럼 답한다 (app/core/authz.py)
    try:
        if job.character_id is not None:
            owned_character(db, job.character_id, user)
        if job.story_id is not None:
            owned_story(db, job.story_id, user)
    except AppError as e:
        raise AppError("JOB_NOT_FOUND", "작업을 찾을 수 없어요.", 404) from e
    error = None
    if job.error_code:
        error = ErrorInfo(code=job.error_code, message=job.error_message or "")
    return JobResponse(
        id=job.id,
        type=job.type,
        status=job.status,
        character_id=job.character_id,
        story_id=job.story_id,
        error=error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
