import io
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Character, Job, JointCorrection
from app.schemas.character import (
    Analysis,
    CharacterCreateResponse,
    CharacterResponse,
    JointsUpdateRequest,
)
from app.schemas.common import AIJoint, BBox, ErrorResponse, JobStatus, JobType, Joint
from app.services.jobs import current_joints, run_analyze_job
from app.services.originals import delete_original, purge_expired_originals
from app.services.storage import get_storage

router = APIRouter(prefix="/characters", tags=["characters"])

_ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpg"}
_ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}


def _get_character(db: Session, character_id: uuid.UUID) -> Character:
    character = db.get(Character, character_id)
    if character is None:
        raise AppError("CHARACTER_NOT_FOUND", "캐릭터를 찾을 수 없어요.", 404)
    return character


def to_response(character: Character) -> CharacterResponse:
    storage = get_storage()
    analysis = None
    if character.bbox is not None:
        analysis = Analysis(
            bbox=BBox.model_validate(character.bbox),
            mask_url=storage.url(character.mask_blob_path),
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
        image_width=character.image_width,
        image_height=character.image_height,
        analysis=analysis,
        joints=[Joint.model_validate(j) for j in joints] if joints else None,
        joints_corrected=bool(character.corrections),
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
    background: BackgroundTasks,
    image: UploadFile = File(description="PNG 또는 JPEG, 최대 10MB"),
    keep_original: bool = Form(
        False,
        description="S-13 「원본 그림 보관」. false 면 그림을 떠날 때, 늦어도 보관 시간 뒤 지운다",
    ),
    db: Session = Depends(get_db),
) -> CharacterCreateResponse:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = image.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        limit_mb = max_bytes // 1024 // 1024
        raise AppError("FILE_TOO_LARGE", f"이미지는 {limit_mb}MB 이하만 올릴 수 있어요.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = img.format, img.size
    except UnidentifiedImageError as e:
        raise AppError("INVALID_IMAGE", "이미지 파일을 읽을 수 없어요.") from e
    if fmt not in _ALLOWED_FORMATS:
        raise AppError("INVALID_IMAGE", "PNG 또는 JPG 이미지만 올릴 수 있어요.")

    character_id = uuid.uuid4()
    path = get_storage().save(f"uploads/{character_id}.{_ALLOWED_FORMATS[fmt]}", data)
    character = Character(
        id=character_id,
        upload_blob_path=path,
        image_width=width,
        image_height=height,
        keep_original=keep_original,
    )
    job = Job(type=JobType.analyze, character_id=character_id)
    db.add(character)
    db.flush()
    db.add(job)
    db.commit()

    background.add_task(run_analyze_job, job.id)
    # 지우라는 요청이 오지 않은 지난 그림을 정리한다. 업로드 때마다 도는 가벼운 조회다
    background.add_task(purge_expired_originals)
    return CharacterCreateResponse(character_id=character.id, job_id=job.id, status=job.status)


@router.get(
    "/{character_id}",
    response_model=CharacterResponse,
    responses=_ERRORS,
    summary="캐릭터 조회 (분석 결과, 현재 관절)",
)
def get_character(character_id: uuid.UUID, db: Session = Depends(get_db)) -> CharacterResponse:
    return to_response(_get_character(db, character_id))


@router.patch(
    "/{character_id}/joints",
    response_model=CharacterResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
    summary="사용자 관절 보정값 저장",
)
def update_joints(
    character_id: uuid.UUID, body: JointsUpdateRequest, db: Session = Depends(get_db)
) -> CharacterResponse:
    character = _get_character(db, character_id)
    if character.status != JobStatus.succeeded:
        raise AppError("CHARACTER_NOT_READY", "그림 분석이 끝난 뒤에 관절을 수정할 수 있어요.", 409)
    out_of_image = [
        j.name
        for j in body.joints
        if j.x > character.image_width or j.y > character.image_height
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
    db.commit()
    db.refresh(character)
    return to_response(character)


@router.delete(
    "/{character_id}/original",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="원본 그림 지우기 (S-13 「원본 그림 보관」이 꺼져 있을 때)",
)
def remove_original(character_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """업로드 파일과 AI 세션을 지운다. 관절과 이야기(문장 · 음성 · MP4)는 남는다.

    이후 이 그림으로 이야기를 또 만들면 MP4 없이(`animation_url: null`) 만든다.
    여러 번 불러도 된다.
    """

    delete_original(db, _get_character(db, character_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
