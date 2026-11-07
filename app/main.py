import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.services.storage import SignedStaticFiles

log = logging.getLogger(__name__)
settings = get_settings()

# 배포에서는 API 문서를 끈다 (구조가 그대로 보인다). ENABLE_DOCS 로 바꿀 수 있다
_docs = settings.enable_docs if settings.enable_docs is not None else not settings.is_production
app = FastAPI(
    title="DrawTale Backend",
    version="0.1.0",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """모든 응답에 붙이는 보안 헤더 (2026-11-02).

    그림 · 영상 · 음성은 다른 주소(프론트)에서 불러 쓰므로
    Cross-Origin-Resource-Policy 는 두지 않는다. HTTPS 강제(HSTS)는 Caddy 가 붙인다"""

    res = await call_next(request)
    res.headers.setdefault("X-Content-Type-Options", "nosniff")
    res.headers.setdefault("X-Frame-Options", "DENY")
    res.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        res.headers.setdefault("Cache-Control", "no-store")
    return res


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)
register_error_handlers(app)

app.include_router(health_router)
app.include_router(api_router)

if settings.storage_backend == "local":
    Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    # 서명 · 기한이 맞는 주소만 연다 (storage.LocalStorage.url).
    # 주소를 아는 것만으로는 열리지 않는다 (2026-11-02)
    app.mount("/files", SignedStaticFiles(directory=settings.local_storage_dir), name="files")
    if settings.is_production:
        log.warning(
            "배포(APP_ENV=prod)인데 STORAGE_BACKEND=local 입니다. "
            "FILE_URL_SECRET 을 정하거나 azure 를 쓰세요"
        )
