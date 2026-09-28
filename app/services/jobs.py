"""Background job runners. Each runner opens its own DB session (the request session is closed)."""

import logging
import uuid

from app.core.errors import AppError
from app.db import session as db_session
from app.models import Character, Job, Story
from app.models.mixins import utcnow
from app.schemas.ai import RenderRequest
from app.schemas.common import JobStatus, Joint
from app.services.ai_client import get_ai_client
from app.services.storage import get_storage

log = logging.getLogger(__name__)


def _start(job: Job) -> None:
    job.status = JobStatus.running
    job.started_at = utcnow()


def _succeed(job: Job) -> None:
    job.status = JobStatus.succeeded
    job.finished_at = utcnow()


def _fail(job: Job, code: str, message: str) -> None:
    job.status = JobStatus.failed
    job.error_code = code
    job.error_message = message
    job.finished_at = utcnow()


def current_joints(character: Character) -> list[dict] | None:
    if character.corrections:
        return character.corrections[-1].joints
    return character.ai_joints


def run_analyze_job(job_id: uuid.UUID) -> None:
    with db_session.SessionLocal() as db:
        job = db.get(Job, job_id)
        character = db.get(Character, job.character_id)
        _start(job)
        character.status = JobStatus.running
        db.commit()

        storage = get_storage()
        try:
            ai = get_ai_client((character.image_width, character.image_height))
            path = character.upload_blob_path
            res = ai.analyze(storage.read(path), filename=path.rsplit("/", 1)[-1])
            character.bbox = res.bbox.model_dump()
            character.ai_joints = [j.model_dump(mode="json") for j in res.joints]
            character.model_version = res.model_version
            character.pipeline_version = res.pipeline_version
            character.coordinate_space = res.coordinate_space
            character.processing_time_ms = res.processing_time_ms
            character.status = JobStatus.succeeded
            _succeed(job)
        except AppError as e:
            character.status = JobStatus.failed
            _fail(job, e.code, e.message)
        except Exception:
            log.exception("analyze job %s failed", job_id)
            character.status = JobStatus.failed
            _fail(job, "INTERNAL_ERROR", "그림 분석 중 알 수 없는 오류가 발생했어요.")
        db.commit()


def _mock_story_text(story: Story) -> str:
    return (
        f"오늘 나는 {story.place}에 갔어요. 그런데 {story.problem}. "
        f"그래서 나는 {story.action}. 그랬더니 {story.result}."
    )


def run_story_job(job_id: uuid.UUID) -> None:
    with db_session.SessionLocal() as db:
        job = db.get(Job, job_id)
        story = db.get(Story, job.story_id)
        character = db.get(Character, story.character_id)
        _start(job)
        story.status = JobStatus.running
        db.commit()

        storage = get_storage()
        try:
            # TODO: OpenAI story generation + moderation, TTS
            story.text = _mock_story_text(story)
            ai = get_ai_client((character.image_width, character.image_height))
            ai.render(
                RenderRequest(
                    request_id=str(job.id),
                    image_url=storage.url(character.upload_blob_path),
                    mask_url=storage.url(character.mask_blob_path),
                    joints=[Joint.model_validate(j) for j in current_joints(character)],
                    motion="wave_hello",
                )
            )
            # Mock render has no output file yet: show the original drawing.
            story.animation_blob_path = character.upload_blob_path
            story.status = JobStatus.succeeded
            _succeed(job)
        except AppError as e:
            story.status = JobStatus.failed
            _fail(job, e.code, e.message)
        except Exception:
            log.exception("story job %s failed", job_id)
            story.status = JobStatus.failed
            _fail(job, "INTERNAL_ERROR", "이야기 생성 중 알 수 없는 오류가 발생했어요.")
        db.commit()
