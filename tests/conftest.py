import io
import os
import tempfile

# Tests never touch the shared dev DB: use a throwaway SQLite file and temp storage.
# Must run before any `app` import because settings and the engine are created at import.
_tmp = tempfile.mkdtemp(prefix="drawtale-test-")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_tmp}/test.db"
os.environ["LOCAL_STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["AI_USE_MOCK"] = "true"

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
