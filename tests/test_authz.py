"""주인이 있는 자료는 그 주인만 본다.

아이 그림과 이야기가 담기므로, 주소(UUID)를 아는 것만으로 남의 것을 보거나
고칠 수 있으면 안 된다. 로그인하지 않고 올린 것은 주인이 없으므로 전과 같이 쓴다
(아이는 계정 없이도 끝까지 갈 수 있어야 한다 — F-ACC-01).
"""

from app.core.auth import issue_token
from app.db.session import SessionLocal
from app.models import User
from app.models.mixins import utcnow
from app.schemas.common import JOINT_ORDER

JOINTS = [{"name": n.value, "x": 10.0, "y": 10.0} for n in JOINT_ORDER]


def _token() -> str:
    """새 계정 하나와 그 로그인 토큰."""

    with SessionLocal() as db:
        user = User(terms_agreed_at=utcnow())
        db.add(user)
        db.flush()
        token = issue_token(db, user)
        db.commit()
        return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload(client, png_bytes, headers=None):
    res = client.post(
        "/api/v1/characters",
        files={"image": ("draw.png", png_bytes, "image/png")},
        headers=headers or {},
    )
    assert res.status_code == 202
    return res.json()


def test_anonymous_upload_stays_open(client, png_bytes):
    """로그인하지 않고 올린 그림은 주인이 없다. 전과 같이 주소로 연다."""

    body = _upload(client, png_bytes)
    assert client.get(f"/api/v1/characters/{body['character_id']}").status_code == 200
    assert client.get(f"/api/v1/jobs/{body['job_id']}").status_code == 200


def test_owner_can_read_own_character(client, png_bytes):
    token = _token()
    body = _upload(client, png_bytes, _auth(token))
    res = client.get(f"/api/v1/characters/{body['character_id']}", headers=_auth(token))
    assert res.status_code == 200


def test_stranger_cannot_read_owned_character(client, png_bytes):
    """남의 그림은 **없는 것처럼** 답한다. 403 은 자료가 있다는 것을 알려주는 셈이다."""

    mine = _token()
    body = _upload(client, png_bytes, _auth(mine))
    url = f"/api/v1/characters/{body['character_id']}"

    assert client.get(url).status_code == 404                      # 로그인하지 않은 사람
    assert client.get(url, headers=_auth(_token())).status_code == 404  # 다른 계정


def test_stranger_cannot_change_joints(client, png_bytes):
    mine = _token()
    body = _upload(client, png_bytes, _auth(mine))
    url = f"/api/v1/characters/{body['character_id']}/joints"

    res = client.patch(url, json={"joints": JOINTS}, headers=_auth(_token()))
    assert res.status_code == 404


def test_stranger_cannot_delete_original(client, png_bytes):
    mine = _token()
    body = _upload(client, png_bytes, _auth(mine))
    url = f"/api/v1/characters/{body['character_id']}/original"

    assert client.delete(url, headers=_auth(_token())).status_code == 404
    assert client.delete(url, headers=_auth(mine)).status_code == 204


def test_stranger_cannot_see_job(client, png_bytes):
    """Job 은 그림에 딸린 것이라 주인도 그림을 따라간다."""

    mine = _token()
    body = _upload(client, png_bytes, _auth(mine))
    url = f"/api/v1/jobs/{body['job_id']}"

    assert client.get(url).status_code == 404
    assert client.get(url, headers=_auth(mine)).status_code == 200


def test_stranger_cannot_make_or_read_story(client, png_bytes):
    mine = _token()
    body = _upload(client, png_bytes, _auth(mine))
    character_id = body["character_id"]
    # 분석이 끝나야 이야기를 만들 수 있다 (목 엔진은 바로 끝난다)
    assert client.get(f"/api/v1/jobs/{body['job_id']}", headers=_auth(mine)).json()["status"]

    payload = {
        "character_id": character_id,
        "place": "숲", "problem": "길을 잃었어요",
        "action": "달려갔어요", "result": "집에 갔어요",
    }
    assert client.post("/api/v1/stories", json=payload, headers=_auth(_token())).status_code == 404

    made = client.post("/api/v1/stories", json=payload, headers=_auth(mine))
    assert made.status_code == 202
    story_id = made.json()["story_id"]

    assert client.get(f"/api/v1/stories/{story_id}").status_code == 404
    assert client.get(f"/api/v1/stories/{story_id}", headers=_auth(mine)).status_code == 200


def test_activity_follows_the_story_owner(client, png_bytes):
    mine = _token()
    body = _upload(client, png_bytes, _auth(mine))
    payload = {
        "character_id": body["character_id"],
        "place": "숲", "problem": "길을 잃었어요",
        "action": "달려갔어요", "result": "집에 갔어요",
    }
    story_id = client.post("/api/v1/stories", json=payload, headers=_auth(mine)).json()["story_id"]
    url = f"/api/v1/stories/{story_id}/activity"
    record = {"attempts": 2, "completed": True, "card_count": 4}

    assert client.post(url, json=record, headers=_auth(_token())).status_code == 404
    assert client.post(url, json=record, headers=_auth(mine)).status_code == 201
    assert client.get(url, headers=_auth(_token())).status_code == 404
    assert len(client.get(url, headers=_auth(mine)).json()) == 1
