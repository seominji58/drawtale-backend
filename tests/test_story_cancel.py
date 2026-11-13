"""이야기 만들기 그만두기 (2026-11-04) — S-08 「그만하기」가 서버 작업을 멈추고 「작업 중」 자리를 비운다."""

from app.services import jobs
from tests.test_flow import _upload

STORY = {"place": "학교", "problem": "친구와 다퉜어요", "action": "먼저 사과해요", "result": "다시 사이좋게 놀아요"}


def test_cancelled_job_stops_and_frees_the_next_story(client, png_bytes, monkeypatch):
    character_id = _upload(client, png_bytes)["character_id"]
    monkeypatch.setattr(jobs, "cancelled", lambda job_id: True)   # 돌기 시작하자마자 그만둔 것과 같다

    res = client.post("/api/v1/stories", json={"character_id": character_id, **STORY})
    assert res.status_code == 202, res.text
    job = client.get(f"/api/v1/jobs/{res.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "CANCELLED"

    monkeypatch.setattr(jobs, "cancelled", lambda job_id: False)
    again = client.post("/api/v1/stories", json={"character_id": character_id, **STORY})
    assert again.status_code == 202, again.text   # 같은 계정 · 그림이 BUSY 로 막히지 않는다
    assert client.get(f"/api/v1/jobs/{again.json()['job_id']}").json()["status"] == "succeeded"


def test_cancel_after_finish_keeps_the_story(client, png_bytes):
    character_id = _upload(client, png_bytes)["character_id"]
    res = client.post("/api/v1/stories", json={"character_id": character_id, **STORY}).json()

    out = client.post(f"/api/v1/stories/{res['story_id']}/cancel")
    assert out.status_code == 202 and out.json()["status"] == "succeeded"
    assert not jobs.cancelled(res["job_id"])
    assert client.get(f"/api/v1/stories/{res['story_id']}").status_code == 200


def test_wait_for_clips_gives_up_when_stopped():
    class C:
        motion_clips = {"walk": jobs.PENDING}

    class DB:
        def refresh(self, obj):
            pass

    jobs.wait_for_motion_clips(DB(), C(), {"walk"}, stop=lambda: True)   # 180초를 기다리지 않고 바로 돌아온다
