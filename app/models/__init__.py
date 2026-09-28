# Import every model module here so Alembic autogenerate can see it.
from app.db.base import Base
from app.models.character import Character, JointCorrection
from app.models.job import Job
from app.models.story import Story

__all__ = ["Base", "Character", "JointCorrection", "Job", "Story"]
