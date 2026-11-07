import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ErrorInfo, JobStatus, JobType


class JobResponse(BaseModel):
    id: uuid.UUID
    type: JobType
    status: JobStatus
    character_id: uuid.UUID | None
    story_id: uuid.UUID | None
    error: ErrorInfo | None
    created_at: datetime
    updated_at: datetime
