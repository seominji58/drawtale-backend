"""Background job runners. Each runner opens its own DB session (the request session is closed)."""

import hashlib
import io
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import session as db_session
from app.models import Character, Job, Story
from app.models.mixins import utcnow
from app.schemas.ai import RenderRequest, RenderResult
from app.schemas.common import JobStatus, Joint
from app.schemas.story import StoryLength, VoiceMode
from app.services import card_stories
from app.services.ai_client import get_ai_client
from app.services.book import STAGES, Book, fallback_book
from app.services.originals import delete_original
from app.services import forms
from app.services.page_motions import FALLBACK, NEW_ACTIONS, cap_new, liven
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


# 「그만하기」를 누른 이야기 작업 (2026-11-04). 서버가 하나라 메모리로 충분하다 — 다시 시작하면 돌던 작업도 없다.
# 작업은 단계(대본 · 음성 · 렌더)마다 이것을 보고 멈춘다. 전에는 끝까지 돌아 그동안 같은 계정이 새 이야기를 못 만들었다
_cancelled: set[uuid.UUID] = set()
_cancel_lock = threading.Lock()


class Cancelled(Exception):
    """사용자가 그만둔 작업."""


def cancel_job(job_id: uuid.UUID) -> None:
    with _cancel_lock:
        _cancelled.add(job_id)


def cancelled(job_id: uuid.UUID) -> bool:
    with _cancel_lock:
        return job_id in _cancelled


def _stop_if_cancelled(job_id: uuid.UUID) -> None:
    if cancelled(job_id):
        raise Cancelled


def _fail(job: Job, code: str, message: str) -> None:
    job.status = JobStatus.failed
    job.error_code = code
    job.error_message = message
    job.finished_at = utcnow()


def current_joints(character: Character) -> list[dict] | None:
    if character.corrections:
        return character.corrections[-1].joints
    return character.ai_joints


def facing_of(joints: list[dict] | None) -> str | None:
    """옆모습 형태의 머리 쪽 — 머리가 몸 뒤쪽보다 왼쪽이면 left. 4족형은 목 · 엉덩이 (AI 렌더도 같은 기준으로
    동작을 뒤집는다), 사슬형 · 지느러미형은 머리 · 꼬리 끝, 날개형은 머리 · 몸통."""
    at = {j["name"]: j for j in joints or []}
    for front, back in (("neck", "hip"), ("head", "tail"), ("head", "body")):     # 4족 · 사슬 · 지느러미 · 날개형
        if front in at and back in at:
            return "left" if at[front]["x"] < at[back]["x"] else "right"
    return None


def run_analyze_job(job_id: uuid.UUID, facing: str | None = None) -> None:
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
            res = ai.analyze(storage.read(path), filename=path.rsplit("/", 1)[-1], form=character.form, facing=facing)
            character.bbox = res.bbox.model_dump()
            character.ai_joints = [j.model_dump(mode="json") for j in res.joints]
            character.detection_confidence = res.confidence
            character.joints_found = res.joints_found
            character.ai_request_id = res.request_id
            character.model_version = res.model_version
            character.pipeline_version = res.pipeline_version
            character.coordinate_space = res.coordinate_space
            character.processing_time_ms = res.processing_time_ms
            character.status = JobStatus.succeeded
            if res.request_id:
                cutout = ai.cutout(res.request_id)
                if cutout:
                    character.cutout_blob_path = storage.save(f"cutouts/{character.id}.png", cutout)
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
# 카드의 동작 열(손 흔들기 · 걷기 · 뛰기 · 대기 · 사라지기)을 따른다 (2026-11-03).
# 걷기 · 뛰기는 CMU Mocap, 춤은 Meta dab 을 순화한 것 (drawtale-ai runtime/ai_server/motions).
# 대기 · 사라지기는 동작 파일이 없어 손 흔들기(기본)로 둔다. 위에서부터 먼저 맞는 낱말을 쓴다
# — 「다가가서 도와줬어요」는 걷기 카드라 「도와」(손 흔들기)보다 「다가가」가 먼저다.
ACTION_MOTIONS: dict[str, str] = {
    "달려": "run",
    "뛰": "run",
    "걸": "walk",
    "다가가": "walk",
    "나섰": "walk",
    "점프": "jumping_gentle",
    "춤": "dab_gentle",
    "운동": "jumping_gentle",
    "흔들": "wave_hello_gentle",
    "불렀": "wave_hello_gentle",
    "물어": "wave_hello_gentle",
    "도와": "wave_hello_gentle",
    "인사": "wave_hello_gentle",
    "사과": "wave_hello_gentle",
}
DEFAULT_MOTION = "wave_hello_gentle"
# 렌더 동작 → 쪽 동작 이름 (화면 S-09 장면 층이 쓰는 이름, PAGE_MOTIONS 의 반대)
MOTION_ACTION = {
    "run": "run", "walk": "walk", "jumping_gentle": "jump", "dab_gentle": "dance", "wave_hello_gentle": "wave",
    **{a: a for a in NEW_ACTIONS},
}

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
    # 사람이 아닌 형태(4족형)는 손 흔들기가 없다 — 걷기로 (pt_docs 19 6장)
    motion = DEFAULT_MOTION if forms.render_motions(character.form) is None else "walk"
    try:
        result = render_clip(character, motion)
    except Exception:
        log.exception("미리보기 렌더 실패, 브라우저 애니메이션으로 대신합니다")
        return None
    if not result.content:  # 목 AI
        return None
    return save_clip(storage, f"previews/{character.id}-{uuid.uuid4().hex[:8]}.mp4", result)


def sprite_path_for(mp4_path: str) -> str:
    """동작 영상 옆에 두는 투명 프레임 묶음 경로 (`walk.mp4` → `walk.sprite.webp`).

    따로 칸을 두지 않고 이름으로 잇는다 — 캐릭터의 motion_clips · 쪽의 animation_blob_path
    그대로 찾는다"""
    return mp4_path.rsplit(".", 1)[0] + ".sprite.webp"


def sprite_meta_path(mp4_path: str) -> str:
    """프레임 묶음의 장 수 · 그림 자리 (`walk.sprite.json`).

    화면은 다른 주소 그림의 픽셀을 못 읽어서(CORS) 서버가 쪽 정보로 준다"""
    return mp4_path.rsplit(".", 1)[0] + ".sprite.json"


def save_clip(storage, path: str, result: RenderResult) -> str:
    """MP4 를 저장하고, 투명 프레임 묶음이 있으면 옆에 같이 저장한다. MP4 경로를 준다."""
    saved = storage.save(path, result.content)
    if result.sprite:
        storage.save(sprite_path_for(saved), result.sprite)
        meta = {"frames": result.sprite_frames, "box": result.sprite_box or [0, 0, 1, 1],
                "in_place": result.sprite_in_place}
        storage.save(sprite_meta_path(saved), json.dumps(meta).encode())
    return saved


def read_sprite_meta(storage, mp4_path: str) -> dict | None:
    """저장해 둔 묶음 정보. 없거나 깨졌으면 None (화면은 영상으로)"""
    try:
        meta = json.loads(storage.read(sprite_meta_path(mp4_path)))
        return {"frames": int(meta["frames"]), "box": [float(v) for v in meta["box"]][:4]}
    except Exception:  # noqa: BLE001
        return None


# 몸 전체가 옆으로 가는 동작. 예전 AI 는 이 이동을 렌더 칸 안에 그대로 그려 칸 끝을 넘었다(잘림).
# 묶음 정보에 in_place 가 없는 이 동작은 이야기를 만들 때 한 번 다시 렌더해 캐릭터 쪽 것도 바꿔 둔다 (2026-11-03)
TRAVEL_MOTIONS = {"walk", "run"}


def sprite_needs_rerender(storage, mp4_path: str, motion: str) -> bool:
    """예전 AI 가 만든(제자리가 아닌) 걷기 · 뛰기 묶음인지. 묶음이 없는 것은 해당 없음"""
    if motion not in TRAVEL_MOTIONS:
        return False
    try:
        meta = json.loads(storage.read(sprite_meta_path(mp4_path)))
    except Exception:  # noqa: BLE001
        return False
    # 다시 렌더했는데 AI 가 아직 예전 판이었으면 표시해 두고 이야기마다 되풀이하지 않는다
    return not meta.get("in_place") and not meta.get("old_ai")


def copy_clip(storage, src: str, dst: str) -> tuple[str, bool]:
    """미리 만든 동작 영상(과 프레임 묶음)을 이야기 폴더로 복사한다.

    (영상 경로, 묶음도 복사했는지)를 돌려준다.
    프레임 묶음이 없으면(예전 그림 · 예전 AI 서버) 영상만"""
    saved = storage.save(dst, storage.read(src))
    try:
        storage.save(sprite_path_for(saved), storage.read(sprite_path_for(src)))
        storage.save(sprite_meta_path(saved), storage.read(sprite_meta_path(src)))
        return saved, True
    except Exception:  # noqa: BLE001
        return saved, False


def render_clip(character: Character, motion: str) -> RenderResult:
    """짧은 동작 영상 (미리보기 · 쪽 동작) + 투명 프레임 묶음.

    동작 전체에서 PREVIEW_MAX_FRAMES 만 골라 그린다."""

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
    return result


# 긴 이야기 쪽 동작 중 미리 렌더해 둘 것 (손 흔들기는 미리보기가 맡는다).
# 이야기를 만들 때 렌더하면 한 편이 2~5분 걸려서(2026-10-12 실측) 캐릭터를 만든 직후
# 미리보기에 이어 렌더한다 — 아이가 이름을 짓고 카드를 고르는 동안 끝난다
# 2026-11-03: 걷기 · 뛰기 · 점프 · 춤. 자주 나오는 순서로 그린다 (VM 2코어에서 하나에 10초 안팎)
# 카드 이야기 512가지에서 많이 쓰는 순 (걷기 1,057쪽 · 점프 387 · 뛰기 316 · 춤 308, 2026-11-03) — 이야기가 필요한 것만 기다리므로
# 자주 쓰는 것이 먼저 끝나야 덜 기다린다. 숨쉬기(2026-11-04)는 가만히 쪽(45%)이 되어 거의 모든 이야기에 나온다.
# 다른 새 동작은 미리 만들지 않고 이야기에 나올 때 렌더한다 (page_motions.NEW_PER_STORY)
PRERENDER_MOTIONS = ("walk", "jumping_gentle", "breathe", "run", "dab_gentle")
# 미리 렌더 중인 동작의 자리 (motion_clips 값). 실패는 None, 끝나면 경로
PENDING = ""
# 이야기를 만들 때 미리 렌더가 아직 돌고 있으면 기다리는 시간. 넘으면 그 쪽들은 멈춘 그림
PRERENDER_WAIT_SECONDS = 180


def start_previews(character: Character) -> list[str]:
    """미리보기 · 쪽 동작을 다시 만들 준비. 지울 옛 파일 경로를 돌려준다 (커밋한 뒤 지운다)."""

    old = [p for p in (character.preview_blob_path, *(character.motion_clips or {}).values()) if p]
    character.preview_blob_path, character.preview_status = None, "rendering"
    character.motion_clips = {}
    # 옆의 프레임 묶음도 — 없는 것을 지우는 것은 아무 일도 하지 않는다
    return old + [sprite_path_for(p) for p in old] + [sprite_meta_path(p) for p in old]


def wants_preview(character: Character) -> bool:
    """미리보기를 만들 수 있는 그림인지. 목 AI 는 MP4 를 만들지 않는다."""

    return (
        bool(character.ai_request_id)
        and character.original_deleted_at is None
        and not get_settings().ai_use_mock
    )


def _store_previews(db, character: Character, storage) -> None:
    """S-05 미리보기를 먼저 만들어 커밋하고(S-05 가 기다린다), 이어서 쪽 동작 영상을 **하나씩 끝날 때마다** 커밋한다.

    이야기는 자기에게 필요한 동작만 끝나면 시작한다 (wait_for_motion_clips) — 전에는 네 동작이 모두 끝나야 시작해
    캐릭터를 만든 직후 이야기를 만들면 한참 기다렸다 (2026-11-03 사용자 「렌더링 시간이 점점 오래」).
    렌더하는 사이 관절을 다시 고쳤거나 원본을 지웠으면(motion_clips 가 바뀜) 옛 관절로 만든 영상은 버린다."""

    character.preview_blob_path = make_preview(character, storage)
    character.preview_status = "ready" if character.preview_blob_path else "failed"
    db.commit()
    db.refresh(character)
    if character.motion_clips != {}:
        return  # 그사이 다시 시작했거나 원본을 지웠다
    allowed = forms.render_motions(character.form)
    motions = [m for m in PRERENDER_MOTIONS if allowed is None or m in allowed]   # 형태가 할 수 있는 것만
    written: dict[str, str | None] = {m: PENDING for m in motions}
    character.motion_clips = dict(written)
    db.commit()
    for motion in motions:
        try:
            result = render_clip(character, motion)
        except Exception:
            log.exception("쪽 동작 %s 미리 렌더 실패", motion)
            result = None
        path = (
            save_clip(storage, f"previews/{character.id}-{uuid.uuid4().hex[:8]}-{motion}.mp4", result)
            if result and result.content
            else None
        )
        db.refresh(character)
        if character.motion_clips != written:
            # 옛 관절로 만든 것 — 지금 것과 이미 만든 것 모두 버린다
            for done in [path, *written.values()]:
                if done:
                    for f in (done, sprite_path_for(done), sprite_meta_path(done)):
                        storage.delete(f)
            return
        written = {**written, motion: path}
        character.motion_clips = dict(written)
        db.commit()


def run_preview_job(character_id: uuid.UUID) -> None:
    """관절을 고친 뒤 미리보기를 다시 만든다 (PATCH joints 가 부른다)."""

    with db_session.SessionLocal() as db:
        character = db.get(Character, character_id)
        if character is None or character.preview_status != "rendering":
            return
        _store_previews(db, character, get_storage())


# AI 서버가 아직 모르는 동작이면 대신 쓸 것 (백엔드가 AI 보다 먼저 배포될 때)
MOTION_FALLBACK = {"walk": "jumping_gentle", "run": "jumping_gentle", "dab_gentle": "jumping_gentle"}


def render_with_fallback(ai, req: RenderRequest):
    """동작을 렌더한다. AI 서버가 그 동작을 아직 모르면 대신할 동작으로 다시 요청한다.

    걷기 · 뛰기 · 춤(2026-11-03)은 점프로, 그 밖의 순화 동작(*_gentle)은 원래 동작으로 간다.
    AI 배포가 늦어도 이야기 만들기가 `UNKNOWN_MOTION` 으로 실패하지 않게 한다.
    """

    try:
        return ai.render(req)
    except AppError as e:
        if (e.detail or {}).get("ai_code") != "UNKNOWN_MOTION":
            raise
        if req.motion in MOTION_FALLBACK:
            base = MOTION_FALLBACK[req.motion]
        elif req.motion.endswith("_gentle"):
            base = req.motion.removesuffix("_gentle")
        else:
            raise
        log.warning("AI does not know %s yet; rendering %s instead", req.motion, base)
        return ai.render(req.model_copy(update={"motion": base}))


def motion_for(action: str) -> str:
    """행동 문구에 맞는 동작을 고른다. 맞는 것이 없으면 기본 동작을 쓴다."""

    for keyword, motion in ACTION_MOTIONS.items():
        if keyword in action:
            return motion
    return DEFAULT_MOTION


def short_motion(action: str, form: str | None) -> str:
    """짧은 이야기 행동 쪽의 렌더 동작. 그 형태가 못 하는 동작이면 형태별 대신 동작, 그래도 없으면 걷기.

    렌더(render_story)와 응답의 action_motion 이 같은 것을 쓴다 — 전에는 응답이 형태를 몰라 사람 아닌 주인공
    짧은 이야기가 모두 손 흔들기(wave)로 나갔고, 화면은 걷기 영상을 제자리 동작으로 보였다 (11/6 이야기 15편 점검).
    「가만히 기다렸어요」(손 흔들기 칸)는 4족형이면 숨쉬기 — 걷기보다 뜻이 맞다 (pt_docs 19 6장 표, 11/6)."""

    motion = motion_for(action)
    allowed = forms.render_motions(form)
    if allowed is None or motion in allowed:
        return motion
    a = form_action(MOTION_ACTION.get(motion), form, allowed)
    return PAGE_MOTIONS.get(a) or "walk"


def _hero_form(form: str | None) -> dict | None:
    """사람(2족형)이 아닌 주인공의 형태 정의 — 이야기 지시문에 모습을 알린다. 사람이면 None."""
    return None if (form or forms.DEFAULT) == forms.DEFAULT else forms.get(form)


def write_story_text(story: Story, form: str | None = None) -> str:
    """OpenAI로 이야기를 만든다. 키가 없거나 실패하면 템플릿 문장을 쓴다."""

    parts = (story.place, story.problem, story.action, story.result)
    writer = get_story_writer()

    if writer is None:
        log.info("OPENAI_API_KEY가 없어 템플릿 문장을 사용합니다")
        return fallback_text(*parts)

    try:
        return writer.write(*parts, hero_form=_hero_form(form))
    except StoryBlocked:
        raise
    except Exception:
        # 외부 API 문제로 서비스를 멈추지 않는다
        log.exception("이야기 생성 실패, 템플릿 문장으로 대체합니다")
        return fallback_text(*parts)


def write_story_script(story: Story, form: str | None = None) -> Script | None:
    """캐릭터 목소리 판의 대본. 만들 수 없으면 None — 나레이션 판으로 대신한다."""

    writer = get_story_writer()
    if writer is None:
        return None

    try:
        script = writer.write_script(
            story.place, story.problem, story.action, story.result, story.protagonist_name,
            hero_form=_hero_form(form),
        )
    except StoryBlocked:
        raise
    except Exception:
        log.exception("대본 생성 실패, 나레이션 판으로 만듭니다")
        return None
    # 아이가 이름을 짓지 않았으면 GPT 가 지은 이름을 남긴다 (긴 동화책에서 같은 이름을 쓰려고)
    story.protagonist_name = story.protagonist_name or script.protagonist or None
    return script


def write_story_book(story: Story, form: str = forms.DEFAULT) -> Book:
    """긴 이야기 대본. 키가 없거나 실패하면 틀 문장 그림책을 쓴다 (부적절한 내용이면 막는다).
    주인공이 사람이 아닌 형태면(pt_docs 19) 대본 AI 에 그 모습을 알린다 — 손을 쓰는 행동을 쓰지 않게."""

    choices = {s: getattr(story, s) for s in STAGES}
    pages = get_settings().story_pages
    characters = story.voice_mode == VoiceMode.characters
    # 이름은 나레이션 판에서도 받아 두지만(나중에 캐릭터 목소리 판으로 바꿀 때 쓰려고, 2026-11-02)
    # 나레이션 판 글은 「나는」이라 이름을 쓰지 않는다
    name = story.protagonist_name if characters else None
    writer = get_story_writer()
    if writer is None:
        log.info("OPENAI_API_KEY가 없어 틀 문장 그림책을 사용합니다")
        return fallback_book(choices, pages, name, set(story.spoken or []))
    try:
        book = writer.write_book(
            choices,
            set(story.spoken or []),
            pages,
            characters,
            name,
            story.extras,
            hero_form=None if form == forms.DEFAULT else forms.get(form),
        )
    except StoryBlocked:
        raise
    except Exception:
        log.exception("그림책 대본 생성 실패, 틀 문장 그림책으로 대체합니다")
        return fallback_book(choices, pages, name, set(story.spoken or []))
    # 아이가 이름을 짓지 않았으면 GPT 가 지은 이름을 남긴다 (캐릭터 목소리 판)
    story.protagonist_name = story.protagonist_name or book.protagonist or None
    return book


def card_audio_key(stored, story: Story) -> str:
    """미리 만든 이야기 음성을 다시 쓸 열쇠 (pt_docs 13 1-1 「(이야기, 이름)마다 처음 요청 때 만들어 저장」).

    이야기 내용 · 이름 · 읽어주기 · 주인공 목소리 · 음성 업체와 목소리가 같으면 같은 음성이다.
    이야기를 다시 만들면(내용이 바뀌면) 열쇠도 바뀐다.
    나레이션 판은 「나는」으로 쓰고 나레이터 하나로 읽는다. 그래서 이름 · 주인공 목소리를 빼고
    조합마다 한 번만 만든다 (2026-10-29, 이름 6 × 목소리 2 = 열두 벌 중복을 막으려고)."""

    s = get_settings()
    payload = json.dumps(stored.payload, ensure_ascii=False, sort_keys=True)
    name = (story.protagonist_name or "") if "{이름}" in payload else ""
    voice = story.protagonist_voice if story.voice_mode == VoiceMode.characters else ""
    parts = [
        payload, story.length, story.voice_mode, name, voice, s.tts_provider, s.elevenlabs_voice_id,
    ]
    return f"card_audio/{stored.id}/" + hashlib.sha1("".join(map(str, parts)).encode()).hexdigest()[:16]


def _read(storage, path: str) -> bytes | None:
    try:
        return storage.read(path)
    except Exception:
        return None


def cached_page_audio(story: Story, storage, book: Book, key: str) -> list[str | None]:
    """긴 저장본 이야기의 쪽 음성. 만들어 둔 것이 있으면 이 이야기 폴더로 복사하고, 없으면 만들어서 둔다."""

    cast = _read(storage, f"{key}/cast.json")
    pages = [_read(storage, f"{key}/page{p.page:02d}.mp3") for p in book.pages]
    if cast is not None and all(pages):
        log.info("story %s: 만들어 둔 음성을 다시 씁니다 (%s)", story.id, key)
        story.voice_cast = json.loads(cast) or None
        return [storage.save(f"results/{story.id}/page{p.page:02d}.mp3", a) for p, a in zip(book.pages, pages, strict=True)]
    paths = make_page_audio(story, storage, book)
    if all(paths):  # 한 쪽이라도 실패했으면 두지 않는다 — 다음에 다시 만든다
        for p, path in zip(book.pages, paths, strict=True):
            storage.save(f"{key}/page{p.page:02d}.mp3", storage.read(path))
        storage.save(f"{key}/cast.json", json.dumps(story.voice_cast or {}, ensure_ascii=False).encode())
    return paths


def cached_audio(story: Story, storage, script: Script | None, key: str) -> str | None:
    """짧은 저장본 이야기의 음성 하나. 위와 같다."""

    cast = _read(storage, f"{key}/cast.json")
    audio = _read(storage, f"{key}/full.mp3")
    if cast is not None and audio:
        log.info("story %s: 만들어 둔 음성을 다시 씁니다 (%s)", story.id, key)
        story.voice_cast = json.loads(cast) or None
        return storage.save(f"results/{story.id}.mp3", audio)
    path = make_audio(story, storage, script)
    if path:
        storage.save(f"{key}/full.mp3", storage.read(path))
        storage.save(f"{key}/cast.json", json.dumps(story.voice_cast or {}, ensure_ascii=False).encode())
    return path


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

    tts = get_tts(story.narrator_voice_id)
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
                audio = _tts_retry(lambda: tts.dialogue(lines, voices))
                story.voice_cast = voices
            except Exception:
                log.exception("캐릭터 목소리 실패, 나레이터 목소리로 읽습니다")
        if audio is None:
            audio = _tts_retry(lambda: tts.speak(story.text))
        return storage.save(f"results/{story.id}.mp3", audio)
    except Exception:
        log.exception("음성 생성 실패, 음성 없이 진행합니다")
        return None


def render_story(story: Story, character: Character, storage) -> str | None:
    """짧은 이야기의 영상 하나. 행동 문구로 동작을 고른다.

    캐릭터를 만든 직후 미리 렌더한 그 동작(과 투명 프레임 묶음)이 있으면 복사한다 — 긴 이야기와 같이
    S-09 장면 층으로 보이게 (2026-11-03 사용자 「짧은 쪽 이야기에는 지금까지 개발한 것들이 적용 안 된 거 같은데」).
    없으면 렌더해서 영상 옆에 묶음도 둔다."""

    motion = short_motion(story.action, character.form)
    ready = dict(character.motion_clips or {})
    if character.preview_blob_path:
        ready[DEFAULT_MOTION] = character.preview_blob_path
    can_render = bool(character.ai_request_id) and character.original_deleted_at is None
    if ready.get(motion) and not (can_render and sprite_needs_rerender(storage, ready[motion], motion)):
        try:
            path, _ = copy_clip(storage, ready[motion], f"results/{story.id}/{motion}.mp4")
            return path
        except Exception:  # noqa: BLE001
            log.exception("짧은 이야기 %s: 미리 만든 %s 영상을 복사하지 못해 렌더합니다", story.id, motion)

    ai = get_ai_client((character.image_width, character.image_height))
    result = None
    if character.ai_request_id:
        result = render_with_fallback(
            ai,
            RenderRequest(
                request_id=character.ai_request_id,
                joints=[Joint.model_validate(j) for j in current_joints(character)],
                motion=motion,   # 전에는 motion_for(...) 그대로 — 사람 아닌 주인공에 그 형태가 못 하는 동작을 보냈다
            ),
        )
    if result and result.content:
        return save_clip(storage, f"results/{story.id}/{motion}.mp4", result)
    if character.original_deleted_at is None:
        # Mock AI, or analysis done before the AI kept a session: show the drawing.
        return character.upload_blob_path
    # 원본을 지운 그림이다. 움직일 그림이 없으니 MP4 없이 둔다
    # (프론트가 들고 있는 그림을 캔버스로 움직여 보여준다)
    return None


# 긴 이야기의 쪽 동작 → 렌더 동작 (2026-11-03 걷기 · 뛰기 · 춤 등록. 전에는 대부분 점프였다).
# 팔이 뭉개지지 않는 것만 쓴다 (2026-09-29). idle 쪽은 렌더하지 않는다 — 프론트가 멈춘 그림.
# 새 동작 9개(2026-11-04)는 AI 동작 이름이 쪽 동작 이름과 같다. idle 은 page_motions.liven 이 거의 다 바꾼다
PAGE_MOTIONS: dict[str, str | None] = {
    "wave": "wave_hello_gentle",
    "walk": "walk",
    "run": "run",
    "jump": "jumping_gentle",
    "dance": "dab_gentle",
    "idle": None,
    **{a: a for a in NEW_ACTIONS},
}
def fit_form(book: Book, form: str) -> Book:
    """그 형태가 렌더할 수 없는 쪽 동작을 바꾼다 (pt_docs 19 6장). 2족형은 그대로.

    대신 동작을 따라가 렌더할 수 있는 동작이 나오면 그것, 끝까지 없으면 가만히(멈춘 그림). 형태 정의의
    `fallback`(뜻으로 고른 형태별 대신 동작)을 먼저, 없으면 page_motions.FALLBACK(사람 기준)을 따른다 —
    사람 기준만 따르면 물고기 헤엄 · 새 날기 · 뱀 기쁨이 멈춘 그림이 됐다 (pt_docs 20 1-2 b, 11/6)."""
    allowed = forms.render_motions(form)
    if allowed is None:
        return book
    for p in book.pages:
        p.action = form_action(p.action, form, allowed)
    return book


def form_action(action: str | None, form: str | None, allowed: set[str] | None = None) -> str:
    """쪽 동작 하나를 그 형태가 렌더할 수 있는 쪽 동작으로. 끝까지 없으면 idle."""
    if allowed is None:
        allowed = forms.render_motions(form)
        if allowed is None:
            return action or "idle"
    own = forms.fallback(form)
    seen: set[str] = set()
    a = action
    while a and PAGE_MOTIONS.get(a) not in allowed and a not in seen:
        seen.add(a)
        a = own.get(a) or FALLBACK.get(a)
    return a if a and PAGE_MOTIONS.get(a) in allowed else "idle"


# 쪽 음성을 동시에 몇 개까지 만드나. ElevenLabs 요금제마다 동시 요청 한도가 있다
PAGE_AUDIO_WORKERS = 3


# 음성 한 번 실패하면 쉬었다 다시 (ElevenLabs 동시 요청 한도 · 잠깐의 오류). 실패한 쪽은 화면이 읽지 않으므로
# (브라우저 기계 음성이 이야기 중간에 끼던 것, 11/3 사용자 「중간에 가끔 다른 tts 나올 때 있는데 막아줘」) 최대한 만든다
TTS_TRIES = 3
TTS_RETRY_SECONDS = 2.0


def _tts_retry(make):  # noqa: ANN001
    """make() 를 TTS_TRIES 번까지. 마지막 실패는 그대로 올린다."""

    for attempt in range(TTS_TRIES):
        try:
            return make()
        except Exception:
            if attempt == TTS_TRIES - 1:
                raise
            log.warning("음성 만들기 실패 — %.0f초 뒤 다시 (%d/%d)", TTS_RETRY_SECONDS * (attempt + 1), attempt + 2, TTS_TRIES)
            time.sleep(TTS_RETRY_SECONDS * (attempt + 1))
    return None


def make_page_audio(story: Story, storage, book: Book) -> list[str | None]:
    """쪽마다 MP3. 쪽으로 나눠도 읽는 글자 수는 이야기 전체와 같다 (음성 비용이 늘지 않는다).

    캐릭터 목소리 판이고 ElevenLabs 면 대사가 있는 쪽은 인물 목소리로 읽는다. 인물 → 목소리는
    이야기 전체에서 한 번 정해 story.voice_cast 에 남긴다 (쪽마다 바뀌지 않게).
    쪽 하나가 실패하면 그 쪽만 None — 프론트가 브라우저 TTS 로 읽는다.
    """

    tts = get_tts(story.narrator_voice_id)
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
                audio = _tts_retry(lambda: tts.dialogue([Line(s, t, e) for s, t, e in page.lines], voices))
            else:
                audio = _tts_retry(lambda: tts.speak(page.text()))
            return storage.save(f"results/{story.id}/page{page.page:02d}.mp3", audio)
        except Exception:
            log.exception("%d쪽 음성 %d번 실패, 그 쪽은 음성 없이 (화면은 읽지 않는다)", page.page, TTS_TRIES)
            return None

    with ThreadPoolExecutor(PAGE_AUDIO_WORKERS) as ex:
        return list(ex.map(one, book.pages))


def make_page_clips(
    story: Story, character: Character, storage, book: Book, sprites: set[str] | None = None
) -> dict[str, str]:
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
            dst = f"results/{story.id}/{motion}.mp4"
            if ready.get(motion) and can_render and sprite_needs_rerender(storage, ready[motion], motion):
                result = render_clip(character, motion)
                if result.content and result.sprite and result.sprite_in_place:
                    save_clip(storage, ready[motion], result)  # 캐릭터 쪽 것을 바꿔 다음 이야기는 복사만
                else:
                    meta = json.loads(storage.read(sprite_meta_path(ready[motion])))
                    storage.save(sprite_meta_path(ready[motion]), json.dumps({**meta, "old_ai": True}).encode())
            if ready.get(motion):
                # 미리 만든 영상(과 투명 프레임 묶음)을 복사한다.
                # 관절을 고치면 캐릭터 쪽 영상은 지워지므로 이야기에 따로 둔다
                clips[motion], has_sprite = copy_clip(storage, ready[motion], dst)
                if has_sprite and sprites is not None:
                    sprites.add(motion)
            elif motion in ready or not can_render:
                continue  # 미리 렌더가 실패했거나 아직이면 다시 하지 않는다 — 그 쪽은 멈춘 그림
            else:
                result = render_clip(character, motion)  # 예전 그림: 미리 만든 것이 없다
                if result.content:
                    clips[motion] = save_clip(storage, dst, result)
                    if result.sprite and sprites is not None:
                        sprites.add(motion)
        except Exception:
            log.exception("쪽 동작 %s 영상 실패, 그 쪽은 멈춘 그림으로", motion)
            continue
    return clips


def wait_for_motion_clips(
    db, character: Character, needed: set[str] | None = None, stop=lambda: False  # noqa: ANN001
) -> None:
    """미리 렌더가 돌고 있으면 **이 이야기에 필요한 동작**이 끝날 때까지 조금 기다린다 (needed 가 없으면 전부)."""

    def busy() -> bool:
        clips = character.motion_clips
        if clips == {}:
            return True
        if not clips:
            return False
        want = needed if needed is not None else set(clips)
        return any(clips.get(m) == PENDING for m in want)

    deadline = time.monotonic() + PRERENDER_WAIT_SECONDS
    while busy() and time.monotonic() < deadline and not stop():
        time.sleep(2)
        db.refresh(character)


def trim_still(png: bytes) -> bytes:
    """잘라 낸 정지 그림을 그림이 있는 자리만 남긴다 (trim_still_box 의 그림만)."""
    return trim_still_box(png)[0]


def trim_still_box(png: bytes) -> tuple[bytes, tuple[int, int, int, int] | None]:
    """잘라 낸 정지 그림(원본 사진 크기 · 둘레는 투명)을 그림이 있는 자리만 남긴다 (2026-11-03).

    화면 장면 층은 정지 그림 전체를 주인공 키에 맞추고, 움직이는 쪽은 프레임 묶음의 그림 자리만 재서 맞춘다.
    둘레 여백이 그대로면 멈춘 쪽 주인공이 작아져, 쪽을 넘길 때마다 커졌다 작아졌다 (사용자 「상황에 안 맞게 커졌다가
    작아지는」). 읽지 못하면 그대로 둔다"""

    try:
        from PIL import Image

        im = Image.open(io.BytesIO(png))
        im.load()
        box = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox() if im.mode in ("RGBA", "LA") else None
        if not box or box == (0, 0, im.width, im.height):
            return png, (0, 0, im.width, im.height)
        pad = max(2, round(max(box[2] - box[0], box[3] - box[1]) * 0.01))
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
        out = io.BytesIO()
        im.crop(box).save(out, format="PNG", optimize=True)
        return out.getvalue(), box
    except Exception:  # noqa: BLE001
        log.exception("정지 그림 다듬기 실패, 그대로 둡니다")
        return png, None


def still_meta_path(still_path: str) -> str:
    """정지 그림 옆 정보 (`still.png` → `still.json`) — 그림 안 주인공 손 위치"""
    return still_path.rsplit(".", 1)[0] + ".json"


def still_hands(joints: list[dict] | None, box: tuple[int, int, int, int] | None) -> dict | None:
    """손 관절 두 개를 정지 그림 안 비율로 { "l": [x, y], "r": [x, y] } — l 은 그림 왼쪽 손 (2026-11-04).

    화면 장면 층이 대본의 「나는 양동이를 꼭 안고」처럼 주인공이 든 소품을 손에 쥐여 준다.
    아이 그림마다 손 자리가 달라 AI 가 찾은 손 관절을 쓴다 (화면은 다른 주소 그림의 픽셀을 못 읽는다)"""
    if not joints or not box or box[2] <= box[0] or box[3] <= box[1]:
        return None
    pts = [j for j in joints if j.get("name") in ("right_hand", "left_hand")]
    if len(pts) != 2:
        return None
    w, h = box[2] - box[0], box[3] - box[1]
    frac = sorted(([min(1.0, max(0.0, (float(j["x"]) - box[0]) / w)), min(1.0, max(0.0, (float(j["y"]) - box[1]) / h))] for j in pts),
                  key=lambda v: v[0])
    return {"l": [round(v, 3) for v in frac[0]], "r": [round(v, 3) for v in frac[1]]}


def still_body(png: bytes, joints: list[dict] | None, box: tuple[int, int, int, int] | None) -> dict | None:
    """4족형 정지 그림 안 입 · 머리 꼭대기 · 등 자리를 그림 비율로 { "mouth": [x, y], "head": [x, y], "back": [x, y] } (11/6).

    장면 층이 「공을 입에 물고」 · 「모자를 쓰고」 · 「바구니를 등에 얹고」 소품을 놓는다. 전에는 그림 상자 비율로 어림해
    머리가 큰 · 목이 긴 그림에서 어긋났다. 4족형 관절에는 머리가 없어(목은 어깨 바로 앞, 몸통 안) 그림에서 잰다 —
    목보다 머리 쪽으로, 앞다리 위쪽보다 높이 있는 그림 = 머리. 머리 쪽으로 가장 멀리 나간 끝 = 입, 맨 위 = 머리 꼭대기,
    엉덩이와 어깨 사이 몸 윗선 = 등. 못 재면 None (화면은 예전처럼 어림)"""
    if not joints or not box or box[2] <= box[0] or box[3] <= box[1]:
        return None
    j = {p.get("name"): (float(p["x"]) - box[0], float(p["y"]) - box[1]) for p in joints}
    if not all(k in j for k in ("hip", "torso", "neck")):
        return None
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(png))
        im.load()
        if im.mode not in ("RGBA", "LA"):
            return None
        a = im.getchannel("A").point(lambda v: 255 if v > 24 else 0)
        w, h = a.size
        right = j["neck"][0] > j["hip"][0]                     # 머리가 그림 오른쪽
        tops = [j[k][1] for k in ("right_hip_f", "left_hip_f") if k in j]
        legs_top = int(min(tops) if tops else j["torso"][1])  # 앞다리 위쪽 — 이보다 위가 머리
        nx = int(j["neck"][0])
        hx0, hx1 = (nx, w) if right else (0, max(1, nx))
        head = a.crop((hx0, 0, hx1, max(1, legs_top))).getbbox()
        if not head:
            return None
        x0, y0, x1, y1 = head[0] + hx0, head[1], head[2] + hx0, head[3]
        if (x1 - x0) * (y1 - y0) < w * h * 0.004:              # 머리라기엔 너무 작다
            return None
        # 입 = 머리 쪽으로 가장 멀리 나간 줄들(그림 폭 6% 이내) 가운데 가장 아래 줄의 끝 (주둥이 · 코끝).
        # 머리 상자의 아래쪽으로 잡으면 목이 긴 그림(기린)은 목 옆 허공에, 가장 앞선 한 점으로 잡으면 귀가 늘어진
        # 강아지는 귀 끝에 찍혔다 (11/6 시험 그림 — 강아지 · 공룡 · 기린 · 머리 큰 고양이)
        px = a.load()
        front: dict[int, int] = {}
        for y in range(y0, y1):
            xs = range(x1 - 1, x0 - 1, -1) if right else range(x0, x1)
            for x in xs:
                if px[x, y]:
                    front[y] = x
                    break
        if not front:
            return None
        best = max(front.values()) if right else min(front.values())
        near = [y for y, x in front.items() if abs(x - best) <= w * 0.06]
        my = max(near)
        mx = front[my]
        bx = int((j["hip"][0] + j["torso"][0]) / 2)
        col = a.crop((max(0, bx - 2), 0, min(w, bx + 3), h)).getbbox()
        by = col[1] if col else j["hip"][1]
        fr = lambda x, y: [round(min(1.0, max(0.0, x / w)), 3), round(min(1.0, max(0.0, y / h)), 3)]  # noqa: E731
        return {"mouth": fr(mx, my), "head": fr((x0 + x1) / 2, y0), "back": fr(bx, by)}
    except Exception:  # noqa: BLE001
        log.exception("4족형 머리 · 등 자리 재기 실패")
        return None


def read_still_body(storage, still_path: str | None) -> dict | None:
    """저장해 둔 4족형 입 · 머리 · 등 자리. 없으면 None (사람 · 예전 이야기 — 화면이 어림한다)"""
    if not still_path:
        return None
    try:
        body = json.loads(storage.read(still_meta_path(still_path))).get("body")
        return {k: [float(v) for v in body[k]][:2] for k in ("mouth", "head", "back")} if body else None
    except Exception:  # noqa: BLE001
        return None


def read_still_hands(storage, still_path: str | None) -> dict | None:
    """저장해 둔 손 위치. 없거나 깨졌으면 None (예전 이야기 — 화면은 손에 쥐여 주지 않는다)"""
    if not still_path:
        return None
    try:
        hands = json.loads(storage.read(still_meta_path(still_path)))["hands"]
        return {k: [float(v) for v in hands[k]][:2] for k in ("l", "r")} if hands else None
    except Exception:  # noqa: BLE001
        return None


def copy_still(story: Story, character: Character, storage) -> str | None:
    """캐릭터의 잘라 낸 정지 그림을 이 이야기 폴더로 복사한다. 없거나 실패하면 None (2026-11-03).

    화면이 원본 사진을 통째로 보이지 않게 이야기가 자기 그림을 갖는다 — 새로고침하거나 책장에서
    다시 열어도, 원본(과 캐릭터 쪽 사본)을 지운 뒤에도 남는다"""

    if not character.cutout_blob_path:
        return None
    try:
        png, box = trim_still_box(storage.read(character.cutout_blob_path))
        path = storage.save(f"results/{story.id}/still.png", png)
        hands = still_hands(current_joints(character), box)
        body = still_body(png, current_joints(character), box) if character.form == "quadruped" else None
        if hands or body:
            storage.save(still_meta_path(path), json.dumps({"hands": hands, "body": body}).encode())
        return path
    except Exception:
        log.exception("story %s: 정지 그림 복사 실패", story.id)
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
        stop = lambda: cancelled(job_id)  # noqa: E731
        try:
            # 카드로만 고른 이야기는 사람이 읽고 통과시킨 저장본을 꺼낸다 — 대본 0초 (pt_docs 13 1-1)
            # 미리 만든 카드 이야기는 주인공이 사람이라고 보고 썼다(「손을 흔들었어요」) — 다른 형태는 대본 AI 가 새로 (pt_docs 19)
            stored = card_stories.find(db, story) if character.form == forms.DEFAULT else None
            name = card_stories.hero_name(story)
            # 보호자 목소리로 읽는 이야기는 만들어 둔 음성(기본 나레이터)을 쓰지 않고,
            # 이 목소리 음성을 함께 쓰는 곳(card_audio)에 두지도 않는다
            # — 이 이야기 폴더에만 (목소리를 지울 때 같이 지운다)
            shared_audio = stored is not None and not story.narrator_voice_id
            if stored is not None:
                log.info("story %s: 미리 만든 카드 이야기를 씁니다 (%s)", story.id, stored.id)
                if name:
                    story.protagonist_name = name
            if story.length == StoryLength.long:
                # 긴 이야기: 쪽 단위 대본, 쪽마다 음성 · 동작 영상. 이야기 전체 MP3 는 만들지 않는다
                book = (
                    card_stories.book_from_payload(stored.payload, name)
                    if stored is not None
                    else write_story_book(story, character.form)
                )
                # 가만히 쪽을 쪽 글에 맞는 동작으로, 새로 렌더할 동작은 이야기 하나에 셋까지 (pt_docs 17)
                cap_new(
                    liven(book, story.place),
                    can_render=bool(character.ai_request_id) and character.original_deleted_at is None,
                )
                fit_form(book, character.form)
                story.text = book.text()
                _stop_if_cancelled(job_id)
                audio = (
                    cached_page_audio(story, storage, book, card_audio_key(stored, story))
                    if shared_audio
                    else make_page_audio(story, storage, book)
                )
                _stop_if_cancelled(job_id)
                wait_for_motion_clips(db, character, {PAGE_MOTIONS.get(p.action) for p in book.pages} - {None}, stop)
                _stop_if_cancelled(job_id)
                sprites: set[str] = set()   # 투명 프레임 묶음까지 있는 동작
                clips = make_page_clips(story, character, storage, book, sprites)
                data = book.to_json()
                for page, path in zip(data["pages"], audio, strict=True):
                    page["audio_blob_path"] = path
                    page["animation_blob_path"] = clips.get(PAGE_MOTIONS.get(page["action"]) or "")
                    # 투명 프레임 묶음(영상 옆 같은 이름)이 있으면 화면이 장면 층으로 쌓는다.
                    # 없으면 영상
                    clip = page["animation_blob_path"]
                    has = clip and PAGE_MOTIONS.get(page["action"]) in sprites
                    meta = has and read_sprite_meta(storage, clip)
                    if meta:
                        page["sprite_blob_path"] = sprite_path_for(clip)
                        page["sprite_frames"], page["sprite_box"] = meta["frames"], meta["box"]
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
                story.audio_blob_path = (
                    cached_audio(story, storage, script, card_audio_key(stored, story))
                    if shared_audio
                    else make_audio(story, storage, script)
                )
                _stop_if_cancelled(job_id)
                wait_for_motion_clips(db, character, {motion_for(story.action)}, stop)
                _stop_if_cancelled(job_id)
                story.animation_blob_path = render_story(story, character, storage)
            else:
                script = (
                    write_story_script(story, character.form) if story.voice_mode == VoiceMode.characters else None
                )
                story.text = script_text(script) if script else write_story_text(story, character.form)
                story.audio_blob_path = make_audio(story, storage, script)
                _stop_if_cancelled(job_id)
                wait_for_motion_clips(db, character, {motion_for(story.action)}, stop)
                _stop_if_cancelled(job_id)
                story.animation_blob_path = render_story(story, character, storage)

            story.still_blob_path = copy_still(story, character, storage)
            story.status = JobStatus.succeeded
            _succeed(job)
        except Cancelled:
            log.info("story job %s: 사용자가 그만뒀습니다", job_id)
            story.status = JobStatus.failed
            _fail(job, "CANCELLED", "이야기 만들기를 그만뒀어요.")
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
        with _cancel_lock:
            _cancelled.discard(job_id)
