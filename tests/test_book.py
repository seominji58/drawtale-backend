"""긴 이야기 (쪽 단위 그림책) — services/book.py 와 length=long 흐름."""

import json

import pytest

from app.services.book import (
    STAGES,
    book_problems,
    book_prompt,
    book_user_message,
    card_stem,
    fallback_book,
    parse_book,
    scene_problems,
    split_pages,
)
from app.services.voices import NARRATOR, PROTAGONIST

CHOICES = {
    "place": "숲",
    "problem": "길을 잃었어요",
    "action": "물어봤어요",
    "result": "친구를 만났어요",
}


def _page(n, stage, text, action="walk", speaker=NARRATOR, child=""):
    return {
        "page": n,
        "stage": stage,
        "action": action,
        "child_words": child,
        "lines": [{"speaker": speaker, "text": text}],
    }


def _scene(pages, sheet=None, **page_over):
    """쪽마다 기본 장면 계획. page_over 로 쪽 번호별 값을 바꾼다 (예: p3={...})."""

    out = []
    for p in pages:
        sc = {
            "page": p["page"],
            "hero_on_page": True,
            "characters": [],
            "background": "A sunny forest path with tall green trees.",
        }
        sc.update(page_over.get(f"p{p['page']}", {}))
        out.append(sc)
    return {"character_sheet": sheet or {}, "pages": out}


def _raw(pages, order=(1, 2, 4, 6), scene="auto", **extra):
    if scene == "auto":
        scene = _scene(pages)
    data = {"title": "숲 이야기", "pages": pages, "order_pages": list(order), **extra}
    if scene is not None:
        data["scene"] = scene
    return json.dumps(data)


GOOD = [
    _page(1, "place", "오늘 나는 숲에 갔어요."),
    _page(2, "problem", "그런데 길을 잃었어요."),
    _page(3, "problem", "공룡이 나타나서 길을 막았어요.", child="공룡이 나타나서 길을 막았어"),
    _page(4, "action", "그래서 나는 물어봤어요."),
    _page(5, "action", "공룡이 큰 나무를 가리켰어요.", action="idle"),
    _page(6, "result", "드디어 친구를 만났어요.", action="dance"),
    _page(7, "result", "오늘은 정말 멋진 날이었어요."),
]


@pytest.mark.parametrize("pages", [3, 7, 8, 10, 11, 20])
def test_split_pages_sums_and_keeps_order(pages):
    n = split_pages(pages)
    assert list(n) == list(STAGES)
    assert sum(n.values()) == max(7, min(11, pages))
    assert all(v >= 1 for v in n.values())


def test_parse_book_keeps_stage_and_child_words():
    book = parse_book(_raw(GOOD))
    assert [p.stage for p in book.pages] == [
        "place",
        "problem",
        "problem",
        "action",
        "action",
        "result",
        "result",
    ]
    assert book.pages[2].child_words == "공룡이 나타나서 길을 막았어"
    assert book.order_pages == [1, 2, 4, 6]
    assert "숲에 갔어요" in book.text()
    assert book_problems(book, CHOICES, 7, characters=False) == []


def test_parse_book_rejects_mixed_stage_order():
    pages = [GOOD[0], GOOD[3], GOOD[1], *GOOD[4:]]
    with pytest.raises(ValueError):
        parse_book(_raw(pages))


def test_parse_book_fixes_action_and_order_pages():
    pages = [dict(GOOD[0], action="fly"), *GOOD[1:]]
    book = parse_book(_raw(pages, order=(1, 2)))
    assert book.pages[0].action == "idle"
    assert book.order_pages == [1, 2, 4, 6]  # 단계마다 첫 쪽


def test_parse_book_protagonist_name_as_speaker():
    pages = [*GOOD[:3], _page(4, "action", "집이 어디야?", speaker="하루"), *GOOD[4:]]
    book = parse_book(
        _raw(pages, protagonist="하루", characters=[{"name": "하루", "type": "아이-여"}])
    )
    assert book.pages[3].lines[0][0] == PROTAGONIST
    assert "하루" not in book.cast
    assert book.pages[3].text() == "“집이 어디야?”"


def test_parse_scene_fixes_hero_slots_and_unknown_people():
    cast = [{"name": "할머니", "type": "어른-여"}, {"name": "친구", "type": "아이-남"}]
    sheet = {"할머니": "a kind grandmother in a purple cardigan", "친구": "a boy in a red cap"}
    scene = _scene(
        GOOD,
        sheet,
        # 행동 쪽인데 주인공이 없다고 함 → 나오게 고친다
        p4={"hero_on_page": False},
        # 같은 자리 두 번 · 대본에 없는 인물 · 세 번째 인물
        p2={
            "characters": [
                {"name": "할머니", "slot": "right-middle"},
                {"name": "친구", "slot": "right-middle"},
                {"name": "공룡", "slot": "right-bottom"},
            ],
            "background": "할머니 waves near a big tree on a windy afternoon.",
        },
        p1={"hero_on_page": False},
    )
    book = parse_book(_raw(GOOD, scene=scene, characters=cast))
    assert book.pages[3].scene.hero is True
    assert book.pages[0].scene.hero is False
    assert book.pages[1].scene.characters == [("할머니", "right-middle"), ("친구", "right-bottom")]
    assert set(book.character_sheet) == {"할머니", "친구"}
    # 인물 이름(한글)은 지시문에 써도 된다
    assert scene_problems(book) == []
    data = book.to_json()
    assert data["pages"][1]["scene"]["characters"][0] == {"name": "할머니", "slot": "right-middle"}
    assert data["character_sheet"]["친구"] == "a boy in a red cap"


def test_scene_problems_hero_name_text_and_missing():
    # 주인공 이름을 지시문에 쓰면 그림 모델이 다른 아이를 그린다
    scene = _scene(
        GOOD,
        p2={"background": "하루 walks along the path."},
        p3={"background": "A wooden sign that says HELLO."},
    )
    book = parse_book(_raw(GOOD, scene=scene, protagonist="하루"))
    probs = scene_problems(book)
    assert len(probs) == 2 and probs[0].startswith("2쪽") and probs[1].startswith("3쪽")
    assert book_problems(book, CHOICES, 7, characters=False) == probs

    # 빠졌거나 쪽 수가 다르면 장면 계획을 버리고 다시 쓰게 한다
    assert parse_book(_raw(GOOD, scene=None)).pages[0].scene is None
    short = _scene(GOOD[:3])
    book = parse_book(_raw(GOOD, scene=short))
    assert all(p.scene is None for p in book.pages)
    assert scene_problems(book) == ["장면 계획이 없거나 쪽 수가 다르다"]


def test_fallback_book_has_no_scene():
    book = fallback_book(CHOICES, 8)
    assert all(p.scene is None for p in book.pages)
    assert book.to_json()["pages"][0]["scene"] is None


def test_book_problems_finds_missing_card_and_dialogue():
    pages = [GOOD[0], _page(2, "problem", "갑자기 비가 왔어요."), *GOOD[3:]]
    book = parse_book(_raw(pages, order=(1, 2, 3, 5)))
    assert any("길을 잃었어요" in p for p in book_problems(book, CHOICES, 7, characters=False))
    talk = parse_book(_raw([*GOOD[:6], _page(7, "result", "고마워!", speaker="친구")]))
    assert any("대사" in p for p in book_problems(talk, CHOICES, 7, characters=False))


def test_card_stem():
    assert card_stem("길을 잃었어요") == "길을 잃"
    assert card_stem("숲") == "숲"
    assert card_stem("공룡이 나타나서 길을 막았어") == "공룡이 나타나서"


def test_fallback_book_uses_every_choice():
    book = fallback_book(CHOICES, 8)
    assert len(book.pages) == 8
    assert [p.page for p in book.pages] == list(range(1, 9))
    text = book.text()
    for s in STAGES:
        assert CHOICES[s].rstrip("요")[:3] in text
    assert book.order_pages == [next(p.page for p in book.pages if p.stage == s) for s in STAGES]
    named = fallback_book(CHOICES, 8, "하늘")
    assert "하늘은 숲에 갔어요" in named.text()


def test_prompt_and_user_message():
    p = book_prompt(8, characters=False, name=None)
    assert "8쪽" in p and "「나」로 쓴다" in p and "[대사]" not in p
    c = book_prompt(10, characters=True, name="콩이")
    assert "콩이" in c and "[대사]" in c
    msg = book_user_message({**CHOICES, "problem": "공룡이 나왔어"}, {"problem"})
    assert '문제 — 아이가 한 말: "공룡이 나왔어"' in msg
    assert '장소 — 아이가 고른 카드: "숲"' in msg


def test_long_story_flow_without_openai(client, png_bytes):
    """length=long 이면 쪽 단위 대본이 오고, 키가 없으면 틀 문장 그림책으로 만든다."""

    res = client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")})
    character_id = res.json()["character_id"]
    res = client.post(
        "/api/v1/stories",
        json={
            "character_id": character_id,
            **CHOICES,
            "problem": "공룡이 나타났어",
            "length": "long",
            "spoken": ["problem"],
        },
    )
    assert res.status_code == 202, res.text
    story = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert story["status"] == "succeeded"
    assert story["length"] == "long"
    book = story["book"]
    assert len(book["pages"]) == 8
    assert [p["stage"] for p in book["pages"]][0] == "place"
    assert len(book["order_pages"]) == 4
    assert story["text"] == " ".join(p["text"] for p in book["pages"])
    assert "공룡이 나타났어" in story["text"]
    assert [p["child_words"] for p in book["pages"] if p["child_words"]] == ["공룡이 나타났어"]


def test_short_story_has_no_book(client, png_bytes):
    res = client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")})
    res = client.post(
        "/api/v1/stories", json={"character_id": res.json()["character_id"], **CHOICES}
    )
    story = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert story["length"] == "short" and story["book"] is None


def test_extras_lines_and_rules():
    """S-07 에서 더 받은 인물 · 기분 카드(시험, 2026-10-27).

    그 단계 줄 다음에 들어가고 지시문에 규칙이 붙는다.
    """
    from app.services.book import book_prompt, book_user_message

    choices = {
        "place": "숲",
        "problem": "어두워졌어요",
        "action": "숨었어요",
        "result": "친구를 만났어요",
    }
    extras = {"who": "강아지", "feel_problem": "무서웠어요", "feel_result": "기뻤어요"}
    lines = book_user_message(choices, set(), extras).splitlines()
    assert lines[1] == '함께 나오는 인물 — 아이가 고른 카드: "강아지"'
    assert lines[3] == '문제 때 주인공 기분 — 아이가 고른 카드: "무서웠어요"'
    assert lines[-1] == '마지막 주인공 기분 — 아이가 고른 카드: "기뻤어요"'
    assert "아이가 더 고른 것" in book_prompt(8, False, None, extras)
    # 없으면 예전과 같다
    assert book_user_message(choices, set()).count("\n") == 3
    assert "아이가 더 고른 것" not in book_prompt(8, False, None)


class _FakeClaude:
    """messages.create 만 흉내 낸다. answers 를 차례로 돌려주거나, 예외면 던진다."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        from types import SimpleNamespace

        self.calls.append(kw)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=a)], stop_reason="end_turn"
        )


def _writer(monkeypatch, claude):
    from app.services import story_writer as sw

    w = sw.StoryWriter("sk-test")
    w.claude = claude
    monkeypatch.setattr(w, "check_input", lambda text: None)
    return w


def test_book_uses_claude_and_strips_fence(monkeypatch):
    """긴 이야기 대본은 Claude 가 먼저 (2026-10-27). JSON 앞뒤의 ``` 는 떼고 읽는다."""

    claude = _FakeClaude(["```json\n" + _raw(GOOD) + "\n```"])
    w = _writer(monkeypatch, claude)
    book = w.write_book(CHOICES, {"problem"}, 7, False)
    assert book.pages[0].text().startswith("오늘 나는 숲에")
    kw = claude.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"] == {"effort": "medium"}
    assert "그림책 대본" in kw["system"]


def test_book_falls_back_to_openai_when_claude_fails(monkeypatch):
    """Claude 호출이 실패하면 그 시도는 OpenAI 모델로 쓴다."""

    import anthropic
    import httpx

    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    w = _writer(monkeypatch, _FakeClaude([err]))
    used = []

    def fake_openai(**kw):
        from types import SimpleNamespace

        used.append(kw["model"])
        msg = SimpleNamespace(content=_raw(GOOD))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(w.client.chat.completions, "create", fake_openai)
    book = w.write_book(CHOICES, set(), 7, False)
    assert used == ["gpt-5.4-mini"]
    assert len(book.pages) == 7
