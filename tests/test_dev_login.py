"""로컬 시험 계정 로그인 명령 (scripts/dev_login.py, pt_docs 20 4-4)."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from scripts import dev_login


def _token(out: str) -> str:
    return out.strip().rsplit("dev_token=", 1)[1]


def test_issues_a_token_that_logs_in(capsys):
    assert dev_login.main([]) == 0
    token = _token(capsys.readouterr().out)
    me = TestClient(app).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["guardian_pin_set"] is False and me.json()["onboarded"] is False


def test_reuses_one_account_and_sets_pin_and_intro(capsys):
    dev_login.main([])
    first = capsys.readouterr().out
    assert dev_login.main(["--pin", "2580", "--onboarded"]) == 0
    out = capsys.readouterr().out
    assert "다시 씀" in out and first.split("(")[1].split(")")[0] in out   # 같은 계정
    token = _token(out)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=auth).json()
    assert me["guardian_pin_set"] is True and me["onboarded"] is True
    assert client.post("/api/v1/me/guardian-pin/check", json={"pin": "2580"}, headers=auth).status_code == 200
    # --fresh 면 처음 상태로
    dev_login.main(["--fresh"])
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_token(capsys.readouterr().out)}"}).json()
    assert me["guardian_pin_set"] is False and me["onboarded"] is False


def test_refuses_outside_local(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    assert dev_login.main([]) == 2
    assert "dev_token" not in capsys.readouterr().out


def test_rejects_a_bad_pin(capsys):
    assert dev_login.main(["--pin", "12a4"]) == 2
