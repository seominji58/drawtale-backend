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
os.environ["RATE_LIMIT_ENABLED"] = "false"
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
    """로그인한 보호자. 가입 없이는 쓸 수 없다 (03 F-ACC-03 · 04, 11-03 코드 반영)."""

    from app.core.auth import issue_token
    from app.db.session import SessionLocal
    from app.models import User
    from app.models.mixins import utcnow

    with SessionLocal() as db:
        user = User(terms_agreed_at=utcnow())
        db.add(user)
        db.flush()
        token = issue_token(db, user)
        db.commit()
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def anon_client() -> TestClient:
    """로그인하지 않은 사람."""

    return TestClient(app)


@pytest.fixture
def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_tts_retry_wait(monkeypatch):
    """음성 재시도 사이 쉬는 시간은 테스트에서 0 (실패를 흉내 내는 테스트가 느려지지 않게)."""
    from app.services import jobs

    monkeypatch.setattr(jobs, "TTS_RETRY_SECONDS", 0.0)
