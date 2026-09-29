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
from app.services.story_writer import StoryBlocked, fallback_text, get_story_writer
from app.services.tts import get_tts

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
            character.ai_request_id = res.request_id
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


# 이야기의 행동 단계를 애니메이션 동작으로 잇는다.
#
# 순화 동작(*_gentle)만 쓴다 (drawtale-ai feat/gentle-motions). 아이 그림은 팔을 몸통에 붙여
# 그리는 경우가 많아, 팔을 내리거나 팔꿈치를 접는 원본 동작에서는 팔이 몸통에 눌려 뭉개진다.
# dab·jumping_jacks·zombie 는 팔을 크게 접거나 내려서 쓰지 않는다.
#
# 키워드는 Frontend 행동 선택지(물어봤어요·달려갔어요·숨었어요·도와줬어요·기다렸어요·
# 크게 불렀어요)와 이전 예시 문구에서 왔다. 활발한 행동은 뛰기, 나머지는 손 흔들기.
ACTION_MOTIONS: dict[str, str] = {
    "달려": "jumping_gentle",
    "뛰": "jumping_gentle",
    "점프": "jumping_gentle",
    "춤": "jumping_gentle",
    "운동": "jumping_gentle",
    "물어": "wave_hello_gentle",
    "도와": "wave_hello_gentle",
    "불렀": "wave_hello_gentle",
    "인사": "wave_hello_gentle",
    "사과": "wave_hello_gentle",
}
DEFAULT_MOTION = "wave_hello_gentle"


def motion_for(action: str) -> str:
    """행동 문구에 맞는 동작을 고른다. 맞는 것이 없으면 기본 동작을 쓴다."""

    for keyword, motion in ACTION_MOTIONS.items():
        if keyword in action:
            return motion
    return DEFAULT_MOTION


def write_story_text(story: Story) -> str:
    """OpenAI로 이야기를 만든다. 키가 없거나 실패하면 템플릿 문장을 쓴다."""

    parts = (story.place, story.problem, story.action, story.result)
    writer = get_story_writer()

    if writer is None:
        log.info("OPENAI_API_KEY가 없어 템플릿 문장을 사용합니다")
        return fallback_text(*parts)

    try:
        return writer.write(*parts)
    except StoryBlocked:
        raise
    except Exception:
        # 외부 API 문제로 서비스를 멈추지 않는다
        log.exception("이야기 생성 실패, 템플릿 문장으로 대체합니다")
        return fallback_text(*parts)


def make_audio(story: Story, storage) -> str | None:
    """이야기를 음성으로 만들어 저장한다. 실패해도 이야기와 애니메이션은 그대로 제공한다."""

    tts = get_tts()
    if tts is None:
        log.info("TTS 키가 없어 음성을 만들지 않습니다")
        return None

    try:
        audio = tts.speak(story.text)
        return storage.save(f"results/{story.id}.mp3", audio)
    except Exception:
        log.exception("음성 생성 실패, 음성 없이 진행합니다")
        return None


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
            story.text = write_story_text(story)
            story.audio_blob_path = make_audio(story, storage)

            ai = get_ai_client((character.image_width, character.image_height))

            if character.ai_request_id:
                result = ai.render(
                    RenderRequest(
                        request_id=character.ai_request_id,
                        joints=[Joint.model_validate(j) for j in current_joints(character)],
                        motion=motion_for(story.action),
                    )
                )
            else:
                result = None

            if result and result.content:
                path = storage.save(f"results/{story.id}.mp4", result.content)
                story.animation_blob_path = path
            else:
                # Mock AI, or analysis done before the AI kept a session: show the drawing.
                story.animation_blob_path = character.upload_blob_path

            story.status = JobStatus.succeeded
            _succeed(job)
        except StoryBlocked:
            story.status = JobStatus.failed
            _fail(
                job,
                "CONTENT_BLOCKED",
                "이야기로 만들 수 없는 내용이 있어요. 다른 것을 골라 주세요.",
            )
        except AppError as e:
            story.status = JobStatus.failed
            _fail(job, e.code, e.message)
        except Exception:
            log.exception("story job %s failed", job_id)
            story.status = JobStatus.failed
            _fail(job, "INTERNAL_ERROR", "이야기 생성 중 알 수 없는 오류가 발생했어요.")
        db.commit()
