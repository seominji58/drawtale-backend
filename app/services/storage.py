"""파일 저장소. `STORAGE_BACKEND` 로 고른다.

- `local`  : LOCAL_STORAGE_DIR 아래에 쓰고 /files 로 내보낸다 (개발용)
- `azure`  : Azure Blob Storage 에 쓰고 기한이 있는 SAS 주소를 내준다 (배포용)

둘은 같은 모양(`save` · `read` · `delete` · `url`)이라 쓰는 쪽 코드는 바뀌지 않는다.
DB 에 담는 경로(`uploads/{id}.png`, `results/{id}.mp4`)도 양쪽이 같다.
"""

import hashlib
import hmac
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote

from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

from app.core.config import get_settings

log = logging.getLogger(__name__)


# 로컬 파일 주소 서명 키. FILE_URL_SECRET 이 없으면 켤 때마다 새로 만든다
_FILE_KEY = (get_settings().file_url_secret or secrets.token_hex(32)).encode()


def file_signature(path: str, exp: int) -> str:
    return hmac.new(_FILE_KEY, f"{path}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


class SignedStaticFiles(StaticFiles):
    """서명 · 기한이 맞는 요청만 파일을 준다 (2026-11-02 보안).

    전에는 /files 가 저장소 폴더 전체를 그대로 내보내서, 주소만 알면 아이 원본 그림 · 영상 · 음성을
    로그인 · 주인 확인 없이 열 수 있었다. 이제 API 가 내준 주소(?exp=&sig=)만 열린다.
    맞지 않으면 404
    """

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            path = self.get_path(scope).replace("\\", "/")
            q = parse_qs(scope.get("query_string", b"").decode())
            exp, sig = (q.get("exp") or [""])[0], (q.get("sig") or [""])[0]
            ok = exp.isdigit() and int(exp) >= time.time() and hmac.compare_digest(
                sig, file_signature(path, int(exp))
            )
            if not ok:
                await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


class LocalStorage:
    def __init__(self, root: str, public_base_url: str) -> None:
        self.root = Path(root)
        self.public_base_url = public_base_url.rstrip("/")

    def save(self, path: str, data: bytes) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def delete(self, path: str | None) -> None:
        """파일을 지운다. 이미 없으면 그냥 넘어간다."""
        if path:
            (self.root / path).unlink(missing_ok=True)

    def url(self, path: str | None) -> str | None:
        """기한 · 서명이 붙은 주소 (azure SAS 와 같은 구실)."""
        if not path:
            return None
        exp = int(time.time()) + get_settings().file_url_hours * 3600
        sig = file_signature(path, exp)
        return f"{self.public_base_url}/files/{quote(path)}?exp={exp}&sig={sig}"


# 확장자 → 브라우저가 바로 재생할 수 있게 붙여 주는 형식
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".gif": "image/gif",
}


class AzureBlobStorage:
    """Azure Blob Storage. 컨테이너는 비공개이고, 읽기는 기한이 있는 SAS 주소로만 연다.

    아이가 그린 그림과 만든 이야기가 담기므로 주소를 아는 누구나 볼 수 있게 두지 않는다.
    주소는 응답을 만들 때마다 새로 발급하므로 기한이 짧아도 문제가 없다.
    """

    def __init__(self, connection_string: str, container: str, sas_hours: int) -> None:
        from azure.storage.blob import BlobServiceClient

        self.service = BlobServiceClient.from_connection_string(connection_string)
        self.container = container
        self.sas_hours = sas_hours
        self.client = self.service.get_container_client(container)

    def _blob(self, path: str):
        return self.client.get_blob_client(path)

    def save(self, path: str, data: bytes) -> str:
        from azure.storage.blob import ContentSettings

        content_type = CONTENT_TYPES.get(Path(path).suffix.lower())
        self._blob(path).upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type) if content_type else None,
        )
        return path

    def read(self, path: str) -> bytes:
        return self._blob(path).download_blob().readall()

    def delete(self, path: str | None) -> None:
        if not path:
            return
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._blob(path).delete_blob()
        except ResourceNotFoundError:
            pass

    def url(self, path: str | None) -> str | None:
        if not path:
            return None
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        token = generate_blob_sas(
            account_name=self.service.account_name,
            container_name=self.container,
            blob_name=path,
            account_key=self.service.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(hours=self.sas_hours),
        )
        return f"{self._blob(path).url}?{token}"


@lru_cache
def _azure_storage(connection_string: str, container: str, sas_hours: int) -> AzureBlobStorage:
    """Blob 클라이언트는 만드는 비용이 있어 설정이 같으면 다시 쓴다."""
    return AzureBlobStorage(connection_string, container, sas_hours)


def get_storage() -> LocalStorage | AzureBlobStorage:
    s = get_settings()

    if s.storage_backend == "azure":
        if not (s.azure_storage_connection_string and s.azure_blob_container):
            raise RuntimeError(
                "STORAGE_BACKEND=azure 인데 AZURE_STORAGE_CONNECTION_STRING "
                "또는 AZURE_BLOB_CONTAINER 가 비어 있습니다."
            )
        return _azure_storage(
            s.azure_storage_connection_string, s.azure_blob_container, s.azure_sas_hours
        )

    return LocalStorage(s.local_storage_dir, s.public_base_url)
