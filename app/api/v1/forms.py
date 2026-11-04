"""형태 목록 — 화면 「누구를 그렸어요?」 · 관절 고치기가 쓴다 (pt_docs 19)."""

from fastapi import APIRouter

from app.services import forms

router = APIRouter(prefix="/forms", tags=["forms"])


@router.get("", summary="형태 목록 (그림의 몸 구조)")
def list_forms() -> dict:
    """준비된 형태만. 관절 이름 · 부모 · 화면 이름 · 안내 · 예시 · 장면에 세우는 방법.
    화면은 관절 이름을 코드에 적지 않고 여기서 받아 그린다. 준비 안 된 형태는 보내지 않는다(버튼을 숨긴다)."""

    return {"forms": [forms.public(f) for f in forms.all_forms().values() if f.get("ready", True)]}
