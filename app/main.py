from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers

settings = get_settings()

app = FastAPI(title="DrawTale Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

app.include_router(health_router)
app.include_router(api_router)

if settings.storage_backend == "local":
    Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=settings.local_storage_dir), name="files")
