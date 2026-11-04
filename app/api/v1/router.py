from fastapi import APIRouter

from app.api.v1 import auth, characters, forms, jobs, stories, voice

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(characters.router)
api_router.include_router(forms.router)
api_router.include_router(jobs.router)
api_router.include_router(stories.router)
api_router.include_router(auth.router)
api_router.include_router(voice.router)
