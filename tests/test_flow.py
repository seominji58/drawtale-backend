from app.schemas.common import JOINT_ORDER


def _upload(client, png_bytes):
    res = client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")})
    assert res.status_code == 202, res.text
    return res.json()


def test_full_flow(client, png_bytes):
    created = _upload(client, png_bytes)

    job = client.get(f"/api/v1/jobs/{created['job_id']}").json()
    assert job["status"] == "succeeded"
    assert job["character_id"] == created["character_id"]

    character = client.get(f"/api/v1/characters/{created['character_id']}").json()
    assert character["image_width"] == 400 and character["image_height"] == 600
    assert [j["name"] for j in character["joints"]] == [n.value for n in JOINT_ORDER]
    assert character["analysis"]["coordinate_space"] == "image_px"
    assert character["analysis"]["model_version"] == "mock-v1"
    assert character["analysis"]["confidence"] == 0.95
    assert all(j["score"] == 0.9 for j in character["analysis"]["joints"])
    # 현재 관절에는 점수가 없다. 사용자 보정값과 같은 모양이다
    assert "score" not in character["joints"][0]
    assert character["joints_corrected"] is False

    joints = character["joints"]
    joints[0]["x"] += 10
    res = client.patch(f"/api/v1/characters/{character['id']}/joints", json={"joints": joints})
    assert res.status_code == 200, res.text
    assert res.json()["joints_corrected"] is True
    assert res.json()["joints"][0]["x"] == joints[0]["x"]

    story_req = {
        "character_id": character["id"],
        "place": "학교",
        "problem": "친구와 다퉜어요",
        "action": "먼저 사과해요",
        "result": "다시 사이좋게 놀아요",
    }
    res = client.post("/api/v1/stories", json=story_req)
    assert res.status_code == 202, res.text
    story_job = client.get(f"/api/v1/jobs/{res.json()['job_id']}").json()
    assert story_job["status"] == "succeeded"

    story = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert "학교" in story["text"]
    assert story["animation_url"].endswith(".png")

    file_path = story["animation_url"].replace("http://127.0.0.1:8000", "")
    assert client.get(file_path).status_code == 200


def test_invalid_image(client):
    files = {"image": ("x.png", b"not an image", "image/png")}
    res = client.post("/api/v1/characters", files=files)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_IMAGE"


def test_joints_must_be_full_skeleton(client, png_bytes):
    created = _upload(client, png_bytes)
    res = client.patch(
        f"/api/v1/characters/{created['character_id']}/joints",
        json={"joints": [{"name": "hip", "x": 1, "y": 1}]},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_joint_outside_image(client, png_bytes):
    created = _upload(client, png_bytes)
    joints = client.get(f"/api/v1/characters/{created['character_id']}").json()["joints"]
    joints[0]["x"] = 5000
    url = f"/api/v1/characters/{created['character_id']}/joints"
    res = client.patch(url, json={"joints": joints})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "JOINT_OUT_OF_IMAGE"


def test_not_found(client):
    res = client.get("/api/v1/characters/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "CHARACTER_NOT_FOUND"
