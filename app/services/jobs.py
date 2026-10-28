"""Background job runners. Each runner opens its own DB session (the request session is closed)."""

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import session as db_session
from app.models import Character, Job, Story
from app.models.mixins import utcnow
from app.schemas.ai import RenderRequest
from app.schemas.common import JobStatus, Joint
from app.schemas.story import StoryLength, VoiceMode
from app.services import card_stories
from app.services.ai_client import get_ai_client
from app.services.book import STAGES, Book, fallback_book
from app.services.originals import delete_original
from app.services.storage import get_storage
from app.services.story_writer import (
    Line,
    Script,
    StoryBlocked,
    fallback_text,
    get_story_writer,
    has_dialogue,
    script_text,
)
from app.services.tts import ElevenLabsSpeech, get_tts
from app.services.voices import NARRATOR, cast_voices

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
            character.detection_confidence = res.confidence
            character.ai_request_id = res.request_id
            character.model_version = res.model_version
            character.pipeline_version = res.pipeline_version
            character.coordinate_space = res.coordinate_space
            character.processing_time_ms = res.processing_time_ms
            character.status = JobStatus.succeeded
            if wants_preview(character):
                start_previews(character)
            _succeed(job)
        except AppError as e:
            character.status = JobStatus.failed
            _fail(job, e.code, e.message)
        except Exception:
            log.exception("analyze job %s failed", job_id)
            character.status = JobStatus.failed
            _fail(job, "INTERNAL_ERROR", "그림 분석 중 알 수 없는 오류가 발생했어요.")
        # 분석에 실패한 그림은 다시 쓰지 않는다. 보관하지 않으면 바로 지운다
        if character.status == JobStatus.failed and not character.keep_original:
            delete_original(db, character)
        db.commit()

        # 미리보기는 분석을 끝낸 뒤 만든다. S-04 는 30초 안에 넘어가야 해서 기다리게 하지 않는다 —
        # S-05 가 preview_status 를 다시 물으며 기다린다
        if character.preview_status == "rendering":
            _store_previews(db, character, storage)


# 이야기의 행동 단계를 애니메이션 동작으로 잇는다.
#
# 순화 동작(*_gentle)만 쓴다 (drawtale-ai feat/gentle-motions). 아이 그림은 팔을 몸통에 붙여
# 그리는 경우가 많아, 팔을 내리거나 팔꿈치를 접는 원본 동작에서는 팔이 몸통에 눌려 뭉개진다.
# dab·jumping_jacks·zombie 는 팔을 크게 접거나 내려서 쓰지 않는다.
#
# 키워드는 행동 카드 16장(pt_docs 05 S-07 「행동 카드 16장」, 2026-10-28 확정 · 프론트 choices.ts)에서 왔다.
# 카드의 동작 열은 손 흔들기 · 걷기 · 뛰기 · 대기 · 사라지기 다섯인데, 순화 동작 파일은 뛰기 · 손 흔들기 둘뿐이라
# 뛰기 카드(「달려」)만 뛰기로 가고 나머지는 손 흔들기(기본)로 간다. 걷기 · 대기 동작이 생기면 여기에 더한다.
ACTION_MOTIONS: dict[str, str] = {
    "달려": "jumping_gentle",
    "뛰": "jumping_gentle",
    "점프": "jumping_gentle",
    "춤": "jumping_gentle",
    "운동": "jumping_gentle",
    "흔들": "wave_hello_gentle",
    "불렀": "wave_hello_gentle",
    "물어": "wave_hello_gentle",
    "도와": "wave_hello_gentle",
    "인사": "wave_hello_gentle",
    "사과": "wave_hello_gentle",
}
DEFAULT_MOTION = "wave_hello_gentle"

# S-05 미리보기. 브라우저 조각 애니메이션은 그림을 네모로 잘라 붙인 것처럼 보여서
# (사용자, 2026-10-08) 분석 직후 Meta 로 렌더해 둔다.
#
# 아래 상한은 **지금 아무 일도 하지 않는다.** AI 서버의 /internal/v1/render 는
# request_id · joints · motion 만 받고, 보낸 max_frames 는 조용히 버려진다 (2026-10-20 확인).
# 그래서 미리보기도 동작 전체를 그려 왔다.
#
# 대신 동작 파일 자체가 짧아져서 상한이 필요 없어졌다 — 손 흔들기가 15프레임(0.5초)이라
# 렌더가 8초 안팎이고 S-04 의 30초 안에 들어간다 (drawtale-ai experiments/render-cost).
# AI 서버에 상한을 붙일지는 정하지 않았다.
PREVIEW_MAX_FRAMES = 150


def make_preview(character: Character, storage) -> str | None:
    """S-05 미리보기 MP4 를 저장하고 경로를 돌려준다.

    실패해도 분석은 성공이다 — 프론트가 브라우저 애니메이션으로 대신한다.
    파일 이름이 매번 달라서 관절을 고친 뒤 브라우저가 옛 영상을 보여주지 않는다.
    """

    if not character.ai_request_id:
        return None
    try:
        content = render_clip(character, DEFAULT_MOTION)
    except Exception:
        log.exception("미리보기 렌더 실패, 브라우저 애니메이션으로 대신합니다")
        return None
    if not content:  # 목 AI
        return None
    return storage.save(f"previews/{character.id}-{uuid.uuid4().hex[:8]}.mp4", content)


def render_clip(character: Character, motion: str) -> bytes:
    """짧은 동작 영상 (미리보기 · 쪽 동작). 동작 전체에서 PREVIEW_MAX_FRAMES 만 골라 그린다."""

    ai = get_ai_client((character.image_width, character.image_height))
    result = render_with_fallback(
        ai,
        RenderRequest(
            request_id=character.ai_request_id,
            joints=[Joint.model_validate(j) for j in current_joints(character)],
            motion=motion,
            max_frames=PREVIEW_MAX_FRAMES,
        ),
    )
    return result.content


# 긴 이야기 쪽 동작 중 미리 렌더해 둘 것 (손 흔들기는 미리보기가 맡는다).
# 이야기를 만들 때 렌더하면 한 편이 2~5분 걸려서(2026-10-12 실측) 캐릭터를 만든 직후
# 미리보기에 이어 렌더한다 — 아이가 이름을 짓고 카드를 고르는 동안 끝난다
PRERENDER_MOTIONS = ("jumping_gentle",)
# 이야기를 만들 때 미리 렌더가 아직 돌고 있으면 기다리는 시간. 넘으면 그 쪽들은 멈춘 그림
PRERENDER_WAIT_SECONDS = 180


def make_motion_clips(character: Character, storage) -> dict[str, str | None]:
    """PRERENDER_MOTIONS 를 렌더해 {동작: 경로}. 실패한 동작은 None (이야기에서는 멈춘 그림)."""

    clips: dict[str, str | None] = {}
    for motion in PRERENDER_MOTIONS:
        try:
            content = render_clip(character, motion)
        except Exception:
            log.exception("쪽 동작 %s 미리 렌더 실패", motion)
            content = None
        clips[motion] = (
            storage.save(f"previews/{character.id}-{uuid.uuid4().hex[:8]}-{motion}.mp4", content)
            if content
            else None
        )
    return clips


def start_previews(character: Character) -> list[str]:
    """미리보기 · 쪽 동작을 다시 만들 준비. 지울 옛 파일 경로를 돌려준다 (커밋한 뒤 지운다)."""

    old = [character.preview_blob_path, *(character.motion_clips or {}).values()]
    character.preview_blob_path, character.preview_status = None, "rendering"
    character.motion_clips = {}
    return [p for p in old if p]


def wants_preview(character: Character) -> bool:
    """미리보기를 만들 수 있는 그림인지. 목 AI 는 MP4 를 만들지 않는다."""

    return (
        bool(character.ai_request_id)
        and character.original_deleted_at is None
        and not get_settings().ai_use_mock
    )


def _store_previews(db, character: Character, storage) -> None:
    """S-05 미리보기를 먼저 만들어 커밋하고(S-05 가 기다린다), 이어서 쪽 동작 영상."""

    character.preview_blob_path = make_preview(character, storage)
    character.preview_status = "ready" if character.preview_blob_path else "failed"
    db.commit()
    clips = make_motion_clips(character, storage)
    db.refresh(character)
    if character.motion_clips == {}:
        character.motion_clips = clips
        db.commit()
    else:  # 그사이 관절을 다시 고쳤거나 원본을 지웠다 — 옛 관절로 만든 영상은 버린다
        for path in clips.values():
            storage.delete(path)


def run_preview_job(character_id: uuid.UUID) -> None:
    """관절을 고친 뒤 미리보기를 다시 만든다 (PATCH joints 가 부른다)."""

    with db_session.SessionLocal() as db:
        character = db.get(Character, character_id)
        if character is None or character.preview_status != "rendering":
            return
        _store_previews(db, character, get_storage())


def render_with_fallback(ai, req: RenderRequest):
    """순화 동작으로 렌더한다. AI 서버가 그 동작을 아직 모르면 원래 동작으로 다시 요청한다.

    순화 동작은 drawtale-ai `feat/gentle-motions` 에 있다. 그게 AI dev 에 합쳐지기 전에도
    이야기 만들기가 `UNKNOWN_MOTION` 으로 실패하지 않게 한다.
    """

    try:
        return ai.render(req)
    except AppError as e:
        gentle = req.motion.endswith("_gentle")
        if not gentle or (e.detail or {}).get("ai_code") != "UNKNOWN_MOTION":
            raise
        base = req.motion.removesuffix("_gentle")
        log.warning("AI does not know %s yet; rendering %s instead", req.motion, base)
        return ai.render(req.model_copy(update={"motion": base}))


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


def write_story_script(story: Story) -> Script | None:
    """캐릭터 목소리 판의 대본. 만들 수 없으면 None — 나레이션 판으로 대신한다."""

    writer = get_story_writer()
    if writer is None:
        return None

    try:
        script = writer.write_script(
            story.place, story.problem, story.action, story.result, story.protagonist_name
        )
    except StoryBlocked:
        raise
    except Exception:
        log.exception("대본 생성 실패, 나레이션 판으로 만듭니다")
        return None
    # 아이가 이름을 짓지 않았으면 GPT 가 지은 이름을 남긴다 (긴 동화책에서 같은 이름을 쓰려고)
    story.protagonist_name = story.protagonist_name or script.protagonist or None
    return script


def write_story_book(story: Story) -> Book:
    """긴 이야기 대본. 키가 없거나 실패하면 틀 문장 그림책을 쓴다 (부적절한 내용이면 막는다)."""

    choices = {s: getattr(story, s) for s in STAGES}
    pages = get_settings().story_pages
    characters = story.voice_mode == VoiceMode.characters
    writer = get_story_writer()
    if writer is None:
        log.info("OPENAI_API_KEY가 없어 틀 문장 그림책을 사용합니다")
        return fallback_book(choices, pages, story.protagonist_name, set(story.spoken or []))
    try:
        book = writer.write_book(
            choices,
            set(story.spoken or []),
            pages,
            characters,
            story.protagonist_name,
            story.extras,
        )
    except StoryBlocked:
        raise
    except Exception:
        log.exception("그림책 대본 생성 실패, 틀 문장 그림책으로 대체합니다")
        return fallback_book(choices, pages, story.protagonist_name, set(story.spoken or []))
    # 아이가 이름을 짓지 않았으면 GPT 가 지은 이름을 남긴다 (캐릭터 목소리 판)
    story.protagonist_name = story.protagonist_name or book.protagonist or None
    return book


def stored_script(payload: dict, name: str | None) -> Script | None:
    """짧은 카드 이야기 저장본 → 캐릭터 목소리 대본. 나레이션 판 저장본(글 하나)이면 None."""

    if "sentences" not in payload:
        return None
    return Script(
        [[Line(s, card_stories.fill_name(t, name), e) for s, t, e in parts] for parts in payload["sentences"]],
        dict(payload.get("cast") or {}),
        name or "",
    )


def book_script(book: Book) -> Script:
    """음성용. 쪽 하나를 대본의 한 덩어리로 본다 — make_audio 가 그대로 읽는다."""

    return Script(
        [[Line(s, t, e) for s, t, e in p.lines] for p in book.pages],
        book.cast,
        book.protagonist,
    )


def make_audio(story: Story, storage, script: Script | None = None) -> str | None:
    """이야기를 음성으로 만들어 저장한다. 실패해도 이야기와 애니메이션은 그대로 제공한다.

    대본에 대사가 있고 ElevenLabs 를 쓰면 대사를 인물 목소리로 읽는다. 인물 → 목소리는
    story.voice_cast 에 남긴다 (긴 동화책에서 쪽을 다시 만들어도 같은 목소리를 쓰려고).
    그게 실패하면 나레이터 하나로 읽는다.
    """

    tts = get_tts()
    if tts is None:
        log.info("TTS 키가 없어 음성을 만들지 않습니다")
        return None

    try:
        audio = None
        if script and has_dialogue(script) and isinstance(tts, ElevenLabsSpeech):
            try:
                lines = [line for parts in script.sentences for line in parts]
                voices = cast_voices(
                    script.cast, story.protagonist_voice, tts.voice_id, (ln.speaker for ln in lines)
                )
                audio = tts.dialogue(lines, voices)
                story.voice_cast = voices
            except Exception:
                log.exception("캐릭터 목소리 실패, 나레이터 목소리로 읽습니다")
        if audio is None:
            audio = tts.speak(story.text)
        return storage.save(f"results/{story.id}.mp3", audio)
    except Exception:
        log.exception("음성 생성 실패, 음성 없이 진행합니다")
        return None


def render_story(story: Story, character: Character, storage) -> str | None:
    """짧은 이야기의 영상 하나. 행동 문구로 동작을 고른다."""

    ai = get_ai_client((character.image_width, character.image_height))
    result = None
    if character.ai_request_id:
        result = render_with_fallback(
            ai,
            RenderRequest(
                request_id=character.ai_request_id,
                joints=[Joint.model_validate(j) for j in current_joints(character)],
                motion=motion_for(story.action),
            ),
        )
    if result and result.content:
        return storage.save(f"results/{story.id}.mp4", result.content)
    if character.original_deleted_at is None:
        # Mock AI, or analysis done before the AI kept a session: show the drawing.
        return character.upload_blob_path
    # 원본을 지운 그림이다. 움직일 그림이 없으니 MP4 없이 둔다
    # (프론트가 들고 있는 그림을 캔버스로 움직여 보여준다)
    return None


# 긴 이야기의 쪽 동작 → 렌더 동작. 렌더는 순화 동작 두 가지뿐이다 (팔이 뭉개지는 문제, 2026-09-29).
# idle 쪽은 렌더하지 않는다 — 프론트가 멈춘 그림을 보여 준다
PAGE_MOTIONS: dict[str, str | None] = {
    "wave": "wave_hello_gentle",
    "walk": "jumping_gentle",
    "run": "jumping_gentle",
    "jump": "jumping_gentle",
    "dance": "jumping_gentle",
    "idle": None,
}
# 쪽 음성을 동시에 몇 개까지 만드나. ElevenLabs 요금제마다 동시 요청 한도가 있다
PAGE_AUDIO_WORKERS = 3


def make_page_audio(story: Story, storage, book: Book) -> list[str | None]:
    """쪽마다 MP3. 쪽으로 나눠도 읽는 글자 수는 이야기 전체와 같다 (음성 비용이 늘지 않는다).

    캐릭터 목소리 판이고 ElevenLabs 면 대사가 있는 쪽은 인물 목소리로 읽는다. 인물 → 목소리는
    이야기 전체에서 한 번 정해 story.voice_cast 에 남긴다 (쪽마다 바뀌지 않게).
    쪽 하나가 실패하면 그 쪽만 None — 프론트가 브라우저 TTS 로 읽는다.
    """

    tts = get_tts()
    if tts is None:
        log.info("TTS 키가 없어 쪽 음성을 만들지 않습니다")
        return [None] * len(book.pages)

    voices: dict[str, str] | None = None
    speakers = [s for p in book.pages for s, _, _ in p.lines if s != NARRATOR]
    if story.voice_mode == VoiceMode.characters and speakers and isinstance(tts, ElevenLabsSpeech):
        voices = cast_voices(book.cast, story.protagonist_voice, tts.voice_id, speakers)
        story.voice_cast = voices

    def one(page) -> str | None:
        try:
            if voices and any(s != NARRATOR for s, _, _ in page.lines):
                audio = tts.dialogue([Line(s, t, e) for s, t, e in page.lines], voices)
            else:
                audio = tts.speak(page.text())
            return storage.save(f"results/{story.id}/page{page.page:02d}.mp3", audio)
        except Exception:
            log.exception("%d쪽 음성 실패, 그 쪽은 브라우저가 읽습니다", page.page)
            return None

    with ThreadPoolExecutor(PAGE_AUDIO_WORKERS) as ex:
        return list(ex.map(one, book.pages))


def make_page_clips(story: Story, character: Character, storage, book: Book) -> dict[str, str]:
    """쪽 동작에 쓸 영상 {동작: 경로}. 동작마다 하나 (지금은 많아야 두 가지).

    손 흔들기는 S-05 미리보기, 통통 뛰기는 캐릭터를 만든 직후 미리 렌더한 영상(motion_clips)을
    이야기 폴더로 복사한다. 미리 만든 것이 없는 예전 그림만 여기서 렌더한다 (150프레임).
    """

    needed = {PAGE_MOTIONS.get(p.action) for p in book.pages} - {None}
    ready = dict(character.motion_clips or {})
    if character.preview_blob_path:
        ready[DEFAULT_MOTION] = character.preview_blob_path
    can_render = bool(character.ai_request_id) and character.original_deleted_at is None
    clips: dict[str, str] = {}
    for motion in sorted(needed):
        try:
            if ready.get(motion):
                # 미리 만든 영상을 복사한다.
                # 관절을 고치면 캐릭터 쪽 영상은 지워지므로 이야기에 따로 둔다
                content = storage.read(ready[motion])
            elif motion in ready or not can_render:
                continue  # 미리 렌더가 실패했거나 아직이면 다시 하지 않는다 — 그 쪽은 멈춘 그림
            else:
                content = render_clip(character, motion)  # 예전 그림: 미리 만든 것이 없다
        except Exception:
            log.exception("쪽 동작 %s 영상 실패, 그 쪽은 멈춘 그림으로", motion)
            continue
        if content:
            clips[motion] = storage.save(f"results/{story.id}/{motion}.mp4", content)
    return clips


def wait_for_motion_clips(db, character: Character) -> None:
    """미리 렌더가 돌고 있으면(motion_clips == {}) 끝날 때까지 조금 기다린다."""

    deadline = time.monotonic() + PRERENDER_WAIT_SECONDS
    while character.motion_clips == {} and time.monotonic() < deadline:
        time.sleep(2)
        db.refresh(character)


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
            # 카드로만 고른 이야기는 사람이 읽고 통과시킨 저장본을 꺼낸다 — 대본 0초 (pt_docs 13 1-1)
            stored = card_stories.find(db, story)
            name = card_stories.hero_name(story)
            if stored is not None:
                log.info("story %s: 미리 만든 카드 이야기를 씁니다 (%s)", story.id, stored.id)
                if name:
                    story.protagonist_name = name
            if story.length == StoryLength.long:
                # 긴 이야기: 쪽 단위 대본, 쪽마다 음성 · 동작 영상. 이야기 전체 MP3 는 만들지 않는다
                book = (
                    card_stories.book_from_payload(stored.payload, name)
                    if stored is not None
                    else write_story_book(story)
                )
                story.text = book.text()
                audio = make_page_audio(story, storage, book)
                wait_for_motion_clips(db, character)
                clips = make_page_clips(story, character, storage, book)
                data = book.to_json()
                for page, path in zip(data["pages"], audio, strict=True):
                    page["audio_blob_path"] = path
                    page["animation_blob_path"] = clips.get(PAGE_MOTIONS.get(page["action"]) or "")
                story.book = data
                story.audio_blob_path = None
                if clips:
                    story.animation_blob_path = next(iter(clips.values()))
                elif character.original_deleted_at is None:
                    story.animation_blob_path = character.upload_blob_path
                else:
                    story.animation_blob_path = None
            elif stored is not None:
                script = stored_script(stored.payload, name)
                story.text = script_text(script) if script else stored.payload["text"]
                story.audio_blob_path = make_audio(story, storage, script)
                story.animation_blob_path = render_story(story, character, storage)
            else:
                script = (
                    write_story_script(story) if story.voice_mode == VoiceMode.characters else None
                )
                story.text = script_text(script) if script else write_story_text(story)
                story.audio_blob_path = make_audio(story, storage, script)
                story.animation_blob_path = render_story(story, character, storage)

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
