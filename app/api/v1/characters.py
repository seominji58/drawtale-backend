import io
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Request,
    Response,
    UploadFile,
    status,
)
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.auth import current_user
from app.core.authz import owned_character
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Character, Job, JointCorrection, User
from app.schemas.character import (
    Analysis,
    CharacterCreateResponse,
    CharacterResponse,
    JointsUpdateRequest,
)
from app.schemas.common import AIJoint, BBox, ErrorResponse, JobStatus, JobType, Joint, validate_full_skeleton
from app.services import forms
from app.services.jobs import (
    current_joints,
    facing_of,
    run_analyze_job,
    run_preview_job,
    start_previews,
    wants_preview,
)
from app.services.originals import delete_original, purge_expired_originals
from app.services.storage import get_storage

router = APIRouter(prefix="/characters", tags=["characters"])

_ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpg"}
_ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}


def to_response(character: Character) -> CharacterResponse:
    storage = get_storage()
    analysis = None
    if character.bbox is not None:
        analysis = Analysis(
            bbox=BBox.model_validate(character.bbox),
            mask_url=storage.url(character.mask_blob_path),
            cutout_url=storage.url(character.cutout_blob_path),
            joints=[AIJoint.model_validate(j) for j in character.ai_joints],
            confidence=character.detection_confidence,
            model_version=character.model_version,
            pipeline_version=character.pipeline_version,
            coordinate_space=character.coordinate_space,
            processing_time_ms=character.processing_time_ms,
        )
    joints = current_joints(character)
    return CharacterResponse(
        id=character.id,
        status=character.status,
        image_url=None
        if character.original_deleted_at
        else storage.url(character.upload_blob_path),
        preview_url=storage.url(character.preview_blob_path),
        preview_status=character.preview_status,
        image_width=character.image_width,
        image_height=character.image_height,
        analysis=analysis,
        joints=[Joint.model_validate(j) for j in joints] if joints else None,
        joints_corrected=bool(character.corrections),
        form=character.form,
        joints_found=character.joints_found,
        keep_original=character.keep_original,
        original_deleted_at=character.original_deleted_at,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CharacterCreateResponse,
    responses=_ERRORS,
    summary="그림 업로드 + 분석 Job 생성",
)
def create_character(
    request: Request,
    background: BackgroundTasks,
    image: UploadFile = File(description="PNG 또는 JPEG, 최대 10MB"),
    keep_original: bool = Form(
        False,
        description="S-13 「원본 그림 보관」. false 면 그림을 떠날 때, 늦어도 보관 시간 뒤 지운다",
    ),
    form: str = Form(
        "biped",
        description="그림의 몸 구조 (GET /api/v1/forms). 없으면 사람(2족형). 화면 「누구를 그렸어요?」 (pt_docs 19)",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CharacterCreateResponse:
    rate_limit.check("analyze", request, user)
    if not forms.get(form).get("ready", True):
        raise AppError("UNKNOWN_FORM", "아직 준비 중인 모양이에요.", 422, {"form": form})
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = image.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        limit_mb = max_bytes // 1024 // 1024
        raise AppError("FILE_TOO_LARGE", f"이미지는 {limit_mb}MB 이하만 올릴 수 있어요.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = img.format, img.size
    except (UnidentifiedImageError, Image.DecompressionBombError) as e:
        raise AppError("INVALID_IMAGE", "이미지 파일을 읽을 수 없어요.") from e
    # 머리만 읽어 크기를 본다 — 작은 파일에 아주 큰 그림을 담으면 풀 때 메모리가 터진다 (2026-11-02)
    if width * height > get_settings().max_image_pixels:
        raise AppError("FILE_TOO_LARGE", "그림이 너무 커요. 더 작은 사진으로 올려 주세요.")
    if fmt not in _ALLOWED_FORMATS:
        raise AppError("INVALID_IMAGE", "PNG 또는 JPG 이미지만 올릴 수 있어요.")

    # 비회원은 교실에서 IP 를 함께 쓰므로 동시 실행은 계정이 있을 때만 막는다
    busy_key = f"analyze:{user.id}" if user else None
    rate_limit.claim(busy_key)
    character_id = uuid.uuid4()
    storage = get_storage()
    path = None

    # 파일을 먼저 올리므로, DB 저장이 실패하면 주인 없는 파일이 남는다. 그때는 되돌린다
    try:
        path = storage.save(f"uploads/{character_id}.{_ALLOWED_FORMATS[fmt]}", data)
        character = Character(
            id=character_id,
            user_id=user.id if user else None,
            upload_blob_path=path,
            image_width=width,
            image_height=height,
            keep_original=keep_original,
            form=form,
        )
        job = Job(type=JobType.analyze, character_id=character_id)
        db.add(character)
        db.flush()
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        rate_limit.release(busy_key)
        if path:
            storage.delete(path)
        raise

    background.add_task(rate_limit.run_then_release, busy_key, run_analyze_job, job.id)
    # 지우라는 요청이 오지 않은 지난 그림을 정리한다. 업로드 때마다 도는 가벼운 조회다
    background.add_task(purge_expired_originals)
    return CharacterCreateResponse(character_id=character.id, job_id=job.id, status=job.status)


@router.get(
    "/{character_id}",
    response_model=CharacterResponse,
    responses=_ERRORS,
    summary="캐릭터 조회 (분석 결과, 현재 관절)",
)
def get_character(
    character_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CharacterResponse:
    return to_response(owned_character(db, character_id, user))


@router.patch(
    "/{character_id}/joints",
    response_model=CharacterResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
    summary="사용자 관절 보정값 저장",
)
def update_joints(
    character_id: uuid.UUID,
    body: JointsUpdateRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CharacterResponse:
    character = owned_character(db, character_id, user)
    if character.status != JobStatus.succeeded:
        raise AppError("CHARACTER_NOT_READY", "그림 분석이 끝난 뒤에 관절을 수정할 수 있어요.", 409)
    # 그 캐릭터 형태의 관절이 빠짐없이 한 번씩 (형태마다 이름이 다르다 — pt_docs 19)
    try:
        body.joints = validate_full_skeleton(body.joints, forms.joint_order(character.form))
    except ValueError as e:
        # 예전과 같은 코드 (요청 검사 실패와 같게) — 화면이 이 코드를 본다
        raise AppError("VALIDATION_ERROR", "관절이 모자라거나 이 모양의 관절이 아니에요.", 422,
                       {"form": character.form}) from e
    out_of_image = [
        j.name for j in body.joints if j.x > character.image_width or j.y > character.image_height
    ]
    if out_of_image:
        raise AppError(
            "JOINT_OUT_OF_IMAGE", "관절이 그림 밖에 있어요.", 400, {"joints": out_of_image}
        )
    db.add(
        JointCorrection(
            character_id=character.id, joints=[j.model_dump(mode="json") for j in body.joints]
        )
    )
    # 미리보기 · 쪽 동작 영상은 고친 관절로 다시 만든다.
    # 그동안 preview_status 는 rendering (프론트가 다시 묻는다)
    old_files: list[str] = []
    if wants_preview(character):
        old_files = start_previews(character)
        background.add_task(run_preview_job, character.id)
    db.commit()
    for path in old_files:
        get_storage().delete(path)
    db.refresh(character)
    return to_response(character)


@router.post(
    "/{character_id}/turn",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CharacterCreateResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
    summary="얼굴 방향 바꾸기 — 옆모습 형태(4족형)를 머리 쪽만 반대로 다시 분석",
)
def turn_character(
    character_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CharacterCreateResponse:
    """S-05 「얼굴이 반대쪽이에요」 (11/4). AI 는 머리 쪽을 굵은 쪽으로 추측해 꼬리가 굵은 그림(악어)에서 틀린다 —
    그러면 목 · 어깨 · 꼬리 · 앞뒤 다리가 모두 뒤집혀 손으로 고치기 어렵다. 같은 그림을 머리 쪽만 정해 다시 분석한다.
    고친 관절 기록은 지운다(뒤집기 전 자리라 맞지 않는다). 미리보기 · 쪽 동작도 다시 만든다. 분석 Job 을 돌려준다."""
    character = owned_character(db, character_id, user)
    if character.status != JobStatus.succeeded:
        raise AppError("CHARACTER_NOT_READY", "그림 분석이 끝난 뒤에 바꿀 수 있어요.", 409)
    if (forms.get(character.form).get("hero") or {}).get("facing") != "auto":
        raise AppError("FORM_HAS_NO_SIDE", "옆모습 친구만 얼굴 방향을 바꿀 수 있어요.", 422, {"form": character.form})
    if character.original_deleted_at is not None or not character.upload_blob_path:
        raise AppError("ORIGINAL_DELETED", "그림이 지워져서 다시 볼 수 없어요. 새로 올려 주세요.", 409)
    rate_limit.check("analyze", request, user)
    facing = "right" if facing_of(current_joints(character)) == "left" else "left"

    busy_key = f"analyze:{user.id}" if user else None
    rate_limit.claim(busy_key)
    try:
        db.query(JointCorrection).filter(JointCorrection.character_id == character.id).delete()
        old_files = start_previews(character)
        character.preview_status = None          # 분석이 끝나면 run_analyze_job 이 다시 연다
        character.status = JobStatus.pending
        job = Job(type=JobType.analyze, character_id=character.id)
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        rate_limit.release(busy_key)
        raise
    for path in old_files:
        get_storage().delete(path)
    background.add_task(rate_limit.run_then_release, busy_key, run_analyze_job, job.id, facing)
    return CharacterCreateResponse(character_id=character.id, job_id=job.id, status=job.status)


@router.delete(
    "/{character_id}/original",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="원본 그림 지우기 (S-13 「원본 그림 보관」이 꺼져 있을 때)",
)
def remove_original(
    character_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    """업로드 파일과 AI 세션을 지운다. 관절과 이야기(문장 · 음성 · MP4)는 남는다.

    이후 이 그림으로 이야기를 또 만들면 MP4 없이(`animation_url: null`) 만든다.
    여러 번 불러도 된다.
    """

    delete_original(db, owned_character(db, character_id, user))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
