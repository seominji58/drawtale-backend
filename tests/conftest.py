import io
import os
import tempfile

# 테스트는 팀 공용 자원(DB · Blob)과 돈이 드는 외부 API 를 절대 건드리지 않는다.
# `.env` 가 azure · 실제 키로 맞춰져 있어도 여기서 덮어쓴다.
# 환경변수가 `.env` 보다 우선하므로, `app` 을 들여오기 전에 먼저 정해야 한다.
_tmp = tempfile.mkdtemp(prefix="drawtale-test-")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_tmp}/test.db"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["AI_USE_MOCK"] = "true"
# 키를 비우면 이야기는 기본 문장으로, 음성은 생략된다.
# 이 둘을 실제로 시험하는 테스트는 monkeypatch 로 직접 키를 넣는다.
os.environ["OPENAI_API_KEY"] = ""
os.environ["TTS_API_KEY"] = ""
os.environ["ELEVENLABS_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(buf, format="PNG")
    return buf.getvalue()
