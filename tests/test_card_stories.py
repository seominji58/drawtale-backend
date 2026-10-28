"""미리 만든 카드 이야기 (pt_docs 13 1-1)."""

from app.db.session import SessionLocal
from app.models.card_story import CardStory
from app.services import card_stories as cs
from app.services.voices import NARRATOR, PROTAGONIST


def _upload(client, png_bytes):
    res = client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")})
    assert res.status_code == 202, res.text
    return res.json()["character_id"]


def _add(status="approved", **kw):
    row = dict(place="숲", problem="길을 잃었어요", action="그 자리에서 기다렸어요", result="길을 찾았어요",
               length="short", voice_mode="narration", payload={"text": "미리 만든 숲 이야기예요."})
    row.update(kw)
    with SessionLocal() as db:
        db.add(CardStory(status=status, **row))
        db.commit()


def _story(client, character_id, **kw):
    req = {"character_id": character_id, "place": "숲", "problem": "길을 잃었어요",
           "action": "그 자리에서 기다렸어요", "result": "길을 찾았어요"}
    req.update(kw)
    res = client.post("/api/v1/stories", json=req)
    assert res.status_code == 202, res.text
    return client.get(f"/api/v1/stories/{res.json()['story_id']}").json()


def test_512_combos_follow_card_table():
    combos = cs.combos()
    assert len(combos) == 512 == len({c.key() for c in combos})
    # 행동 · 결과는 고른 문제의 것만 (05 S-07)
    assert all(c.action in cs.ACTIONS[c.problem] and c.result in cs.RESULTS[c.problem] for c in combos)
    assert not cs.is_card_combo("숲", "길을 잃었어요", "숨었어요", "길을 찾았어요")


def test_name_slot_takes_josa_of_real_name():
    t = cs.templatize("라라가 웃었어요. “라라야, 같이 가자!” 라라는 라라를 불렀어요.")
    assert "라라" not in t
    assert cs.fill_name(t, "콩이") == "콩이가 웃었어요. “콩이야, 같이 가자!” 콩이는 콩이를 불렀어요."
    assert cs.fill_name(t, "민준") == "민준이 웃었어요. “민준아, 같이 가자!” 민준은 민준을 불렀어요."
    assert cs.fill_name(cs.templatize("라라로"), "별") == "별로"
    assert cs.fill_name(cs.templatize("라라와"), None) == "토토와"  # 이름을 짓지 않았으면 기본 이름


def test_approved_card_story_is_used(client, png_bytes):
    _add()
    story = _story(client, _upload(client, png_bytes))
    assert story["status"] == "succeeded"
    assert story["text"] == "미리 만든 숲 이야기예요."


def test_draft_is_not_used(client, png_bytes):
    _add(status="draft")
    story = _story(client, _upload(client, png_bytes))
    assert story["text"] != "미리 만든 숲 이야기예요."  # 사람이 읽기 전에는 꺼내지 않는다


def test_spoken_story_is_written_then(client, png_bytes):
    _add()
    story = _story(client, _upload(client, png_bytes), spoken=["action"])
    assert story["text"] != "미리 만든 숲 이야기예요."  # 아이가 말로 덧붙이면 그때 만든다


def test_characters_short_fills_name(client, png_bytes):
    _add(voice_mode="characters", payload=cs.short_payload(
        sentences=[[(NARRATOR, "라라는 숲에서 길을 잃었어요.", "")], [(PROTAGONIST, "여기서 기다릴래!", "calm")]],
        cast={},
    ))
    story = _story(client, _upload(client, png_bytes), voice_mode="characters", protagonist_name="민준")
    assert story["text"].startswith("민준은 숲에서 길을 잃었어요.")
