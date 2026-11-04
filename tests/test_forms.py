"""형태 — 그림의 몸 구조 (2026-11-04, pt_docs 19). 목 AI 는 2족형 사본 + 시험용 4족형을 안다."""

import pytest

from app.services import forms
from app.services.book import Book, Page, book_prompt
from app.services.jobs import fit_form
from app.services.voices import NARRATOR


@pytest.fixture(autouse=True)
def _fresh_forms():
    forms.reset()
    yield
    forms.reset()


def _upload(client, png_bytes, form=None):
    data = {"form": form} if form else {}
    return client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")}, data=data)


def test_forms_list_for_screen(client):
    body = client.get("/api/v1/forms").json()
    ids = [f["id"] for f in body["forms"]]
    assert ids[:2] == ["biped", "quadruped"]
    quad = body["forms"][1]
    assert quad["kid_label"] == "네 발로 걷는 친구" and len(quad["joints"]) == 15
    assert "motions" not in quad and "retarget" not in quad   # AI 안쪽 값은 화면에 내주지 않는다


def test_quadruped_upload_gets_quadruped_joints_and_checks_edits(client, png_bytes):
    created = _upload(client, png_bytes, "quadruped")
    assert created.status_code == 202, created.text
    character = client.get(f"/api/v1/characters/{created.json()['character_id']}").json()
    assert character["form"] == "quadruped"
    names = [j["name"] for j in character["joints"]]
    assert names == forms.joint_order("quadruped")

    url = f"/api/v1/characters/{character['id']}/joints"
    joints = character["joints"]
    joints[0]["x"] += 5
    assert client.patch(url, json={"joints": joints}).status_code == 200
    human = [{"name": n, "x": 1, "y": 1} for n in forms.joint_order("biped")]
    res = client.patch(url, json={"joints": human})
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_default_upload_is_still_biped(client, png_bytes):
    created = _upload(client, png_bytes)
    character = client.get(f"/api/v1/characters/{created.json()['character_id']}").json()
    assert character["form"] == "biped"
    assert [j["name"] for j in character["joints"]] == forms.joint_order("biped")


def test_unknown_form_is_refused(client, png_bytes):
    res = _upload(client, png_bytes, "octopus")
    assert res.status_code == 422 and res.json()["error"]["code"] == "UNKNOWN_FORM"


def _book(*actions: str) -> Book:
    return Book("t", [Page(i + 1, "place", a, [(NARRATOR, "글", "")]) for i, a in enumerate(actions)], [1])


def test_quadruped_pages_keep_only_what_it_can_do():
    book = fit_form(_book("walk", "march", "run", "hooray", "wave", "idle", "breathe"), "quadruped")
    assert [p.action for p in book.pages] == ["walk", "walk", "run", "idle", "idle", "idle", "idle"]
    same = fit_form(_book("hooray", "wave"), "biped")
    assert [p.action for p in same.pages] == ["hooray", "wave"]


def test_book_prompt_tells_the_hero_form():
    prompt = book_prompt(8, False, None, hero_form=forms.get("quadruped"))
    assert "[주인공 모습]" in prompt and "네 발로 걷는 친구" in prompt
    assert "walk, run, idle" in prompt
    assert prompt.rstrip().endswith("}") or "JSON 으로만 답한다." in prompt
    assert "[주인공 모습]" not in book_prompt(8, False, None)
