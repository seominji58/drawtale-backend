"""카드 이야기 씨앗 (app/seed/card_stories.jsonl.gz) — 저장소의 이야기를 어느 DB 에든 같게 넣는다."""

from app.db.session import SessionLocal
from app.models.card_story import CardStory
from app.seed.card_stories import KEY, export_seed, load_seed, read_seed
from app.services import card_stories as cs


def test_seed_covers_every_card_combo():
    """씨앗은 카드 표의 512조합 × 길이 2 × 읽어주기 2 를 빠짐없이, 모두 통과본으로."""

    rows = read_seed()
    keys = {tuple(d[k] for k in KEY) for d in rows}
    want = {(c.place, c.problem, c.action, c.result, length, mode)
            for c in cs.combos() for length in cs.LENGTHS for mode in cs.VOICE_MODES}
    assert len(rows) == len(keys) == len(want) == 2048
    assert keys == want
    assert {d["status"] for d in rows} == {"approved"}
    # 캐릭터 목소리 판은 이름 자리가 비어 있어야 아이가 지은 이름이 들어간다
    assert all("{이름}" in str(d["payload"]) for d in rows if d["voice_mode"] == "characters")


def test_export_then_load_round_trip(tmp_path):
    with SessionLocal() as db:
        db.add(CardStory(place="숲", problem="길을 잃었어요", action="그 자리에서 기다렸어요", result="길을 찾았어요",
                         length="short", voice_mode="narration", payload={"text": "숲 이야기예요."}, status="approved"))
        db.add(CardStory(place="숲", problem="길을 잃었어요", action="그 자리에서 기다렸어요", result="길을 찾았어요",
                         length="long", voice_mode="narration", payload={"book": {}}, status="draft"))
        db.commit()
        path = tmp_path / "seed.jsonl.gz"
        assert export_seed(db, path) == 1  # draft 는 씨앗에 넣지 않는다

        db.query(CardStory).delete()
        db.commit()
        assert load_seed(db, path) == {"new": 1, "updated": 0, "same": 0}
        assert load_seed(db, path) == {"new": 0, "updated": 0, "same": 1}  # 몇 번 돌려도 같다

        row = db.query(CardStory).one()
        row.payload = {"text": "누가 고친 글"}
        db.commit()
        assert load_seed(db, path) == {"new": 0, "updated": 1, "same": 0}  # 씨앗 내용으로 맞춘다
        assert db.query(CardStory).one().payload == {"text": "숲 이야기예요."}
