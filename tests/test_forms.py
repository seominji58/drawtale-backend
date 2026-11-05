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
    assert quad["kid_label"] == "네 발로 걷는 친구" and len(quad["joints"]) == 18   # 다리 12 · 몸 3 · 꼬리 3
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


def _side(character: dict) -> str:
    at = {j["name"]: j["x"] for j in character["joints"]}
    return "left" if at["neck"] < at["hip"] else "right"


def test_turn_flips_head_side_and_drops_corrections(client, png_bytes):
    """S-05 「얼굴이 반대쪽이에요」 — 머리 쪽만 반대로 다시 분석하고, 고친 관절 기록은 지운다 (11/4)."""
    cid = _upload(client, png_bytes, "quadruped").json()["character_id"]
    url = f"/api/v1/characters/{cid}"
    before = client.get(url).json()
    joints = before["joints"]
    joints[0]["x"] += 5
    assert client.patch(f"{url}/joints", json={"joints": joints}).json()["joints_corrected"] is True

    res = client.post(f"{url}/turn")
    assert res.status_code == 202, res.text
    after = client.get(url).json()
    assert after["status"] == "succeeded" and after["joints_corrected"] is False
    assert _side(after) != _side(before)
    assert client.post(f"{url}/turn").status_code == 202          # 다시 누르면 원래 쪽으로
    assert _side(client.get(url).json()) == _side(before)


def test_turn_is_only_for_side_view_forms(client, png_bytes):
    cid = _upload(client, png_bytes).json()["character_id"]
    res = client.post(f"/api/v1/characters/{cid}/turn")
    assert res.status_code == 422 and res.json()["error"]["code"] == "FORM_HAS_NO_SIDE"


def test_story_tells_hero_form_and_facing(client, png_bytes):
    """이야기 응답에 주인공 형태 · 머리 쪽 — 화면이 세션 관절 없이도(새로고침 · 책장) 옆모습 주인공을 뒤집는다 (11/4)."""
    from tests.test_activity import _STORY

    def story_of(form):
        cid = _upload(client, png_bytes, form).json()["character_id"]
        sid = client.post("/api/v1/stories", json={"character_id": cid, **_STORY, "protagonist_name": "뭉치"}).json()["story_id"]
        return cid, client.get(f"/api/v1/stories/{sid}").json()

    cid, quad = story_of("quadruped")
    assert quad["hero_form"] == "quadruped" and quad["hero_facing"] == _side(client.get(f"/api/v1/characters/{cid}").json())
    _, human = story_of(None)
    assert human["hero_form"] == "biped" and human["hero_facing"] is None


def test_short_story_tells_the_hero_form():
    """짧은 이야기(나레이션 · 캐릭터 목소리 판)도 사람이 아닌 주인공의 모습을 알린다 (11/5 — 고양이가 손을 흔들었다)."""
    from app.services.story_writer import SYSTEM_PROMPT, StoryWriter

    seen: list[str] = []
    writer = object.__new__(StoryWriter)
    writer.check_input = lambda text: None

    def fake(system, user, json_mode, openai_tokens):
        seen.append(system)
        if json_mode:
            return ('{"protagonist": "토토", "lines": [{"speaker": "narrator", "text": "토토는 숲에 갔어요."},'
                    ' {"speaker": "토토", "text": "\\"안녕.\\" 하고 말했어요."}]}')
        return "나는 숲에 갔어요."
    writer.short_completion = fake

    writer.write("숲", "길을 잃었어요", "손을 흔들었어요", "웃었어요", hero_form=forms.get("quadruped"))
    assert seen[-1].startswith(SYSTEM_PROMPT) and "네 발로 걷는 친구" in seen[-1] and "몸을 흔들며" in seen[-1] and "꼬리 이야기는 쓰지 않는다" in seen[-1]
    writer.write("숲", "길을 잃었어요", "손을 흔들었어요", "웃었어요")
    assert seen[-1] == SYSTEM_PROMPT                               # 사람 그림은 그대로
    try:
        writer.write_script("숲", "길을 잃었어요", "손을 흔들었어요", "웃었어요", "토토", hero_form=forms.get("quadruped"))
    except ValueError:
        pass                                                       # 대본 모양은 이 시험의 관심이 아니다
    assert "네 발로 걷는 친구" in seen[-1]


def test_quadruped_story_needs_a_name(client, png_bytes):
    """4족형은 이름이 있어야 이야기를 만든다 — 무슨 동물인지 묻지 않고 이름으로 부른다 (11/5 사용자)."""
    from tests.test_activity import _STORY

    cid = _upload(client, png_bytes, "quadruped").json()["character_id"]
    for name in (None, "  "):
        res = client.post("/api/v1/stories", json={"character_id": cid, **_STORY, "protagonist_name": name})
        assert res.status_code == 422 and res.json()["error"]["code"] == "NAME_REQUIRED"
    assert client.post("/api/v1/stories", json={"character_id": cid, **_STORY, "protagonist_name": "뭉치"}).status_code == 202
    human = _upload(client, png_bytes).json()["character_id"]                  # 사람 그림은 지금처럼 이름 없이도
    assert client.post("/api/v1/stories", json={"character_id": human, **_STORY}).status_code == 202


def test_body_rules_in_both_prompts():
    from app.services.book import short_form_rule

    for text in (book_prompt(8, False, None, hero_form=forms.get("quadruped")), short_form_rule(forms.get("quadruped"))):
        assert "냄새 맡기" in text and "두 발로 서기" in text and "동물 이름으로 부르지 않고" in text


def test_facing_and_body_rules_for_chain_and_finned():
    """사슬형 · 지느러미형 — 머리 쪽은 머리 · 꼬리 끝으로, 이야기 행동은 그 몸으로 (11/5)."""
    from app.services.book import body_rules, short_form_rule
    from app.services.jobs import facing_of

    line = [{"name": "head", "x": 10, "y": 5}, {"name": "b1", "x": 50, "y": 5}, {"name": "tail", "x": 90, "y": 5}]
    assert facing_of(line) == "left"
    assert facing_of([{**j, "x": 100 - j["x"]} for j in line]) == "right"
    assert facing_of([{"name": "neck", "x": 80, "y": 0}, {"name": "hip", "x": 20, "y": 0}]) == "right"
    snake = body_rules({"id": "chain"})
    assert "기어가기" in snake and "걷기 · 뛰기 · 점프" in snake and "동물 이름으로 부르지 않고" in snake
    fish = short_form_rule({"id": "finned", "kid_label": "헤엄치는 친구", "examples": ["물고기"]})
    assert "헤엄치기" in fish and "둥실 떠서" in fish and "빠르게 헤엄쳐 갔어요" in fish


def _book(*actions: str) -> Book:
    return Book("t", [Page(i + 1, "place", a, [(NARRATOR, "글", "")]) for i, a in enumerate(actions)], [1])


def test_quadruped_pages_keep_only_what_it_can_do():
    book = fit_form(_book("walk", "march", "run", "hooray", "wave", "idle", "breathe"), "quadruped")
    # 만세 → 점프 (page_motions.FALLBACK), 숨쉬기는 4족형도 된다 (11/4). 손 흔들기는 대신할 것이 없어 가만히
    assert [p.action for p in book.pages] == ["walk", "walk", "run", "jump", "idle", "idle", "breathe"]
    same = fit_form(_book("hooray", "wave"), "biped")
    assert [p.action for p in same.pages] == ["hooray", "wave"]


def test_book_prompt_tells_the_hero_form():
    prompt = book_prompt(8, False, None, hero_form=forms.get("quadruped"))
    assert "[주인공 모습]" in prompt and "네 발로 걷는 친구" in prompt
    assert "walk, run, jump, breathe, shiver, idle" in prompt    # AI 동작 이름(jumping_gentle)이 아니라 쪽 동작 이름
    assert prompt.rstrip().endswith("}") or "JSON 으로만 답한다." in prompt
    assert "[주인공 모습]" not in book_prompt(8, False, None)
