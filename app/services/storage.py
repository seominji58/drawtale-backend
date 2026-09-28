"""File storage. `local` writes under LOCAL_STORAGE_DIR and serves it at /files.

Azure Blob Storage will implement the same interface later.
"""

from pathlib import Path

from app.core.config import get_settings


class LocalStorage:
    def __init__(self, root: str, public_base_url: str) -> None:
        self.root = Path(root)
        self.public_base_url = public_base_url.rstrip("/")

    def save(self, path: str, data: bytes) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    def url(self, path: str | None) -> str | None:
        if not path:
            return None
        return f"{self.public_base_url}/files/{path}"


def get_storage() -> LocalStorage:
    s = get_settings()
    return LocalStorage(s.local_storage_dir, s.public_base_url)
