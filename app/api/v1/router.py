from fastapi import APIRouter

from app.api.v1 import characters, jobs, stories

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(characters.router)
api_router.include_router(jobs.router)
api_router.include_router(stories.router)
