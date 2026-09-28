import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import get_db
from app.models import Job
from app.schemas.common import ErrorInfo, ErrorResponse
from app.schemas.job import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}},
    summary="분석/이야기 Job 진행 상태 조회 (polling)",
)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise AppError("JOB_NOT_FOUND", "작업을 찾을 수 없어요.", 404)
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
