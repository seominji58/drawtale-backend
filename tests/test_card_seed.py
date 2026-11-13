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


def test_form_seed_apply_and_biped_seed_side_by_side(tmp_path):
    """형태별 씨앗(app/seed/form_stories.py, 마이그레이션 f1a2b3c4d5e6 가 배포 때 넣는다): 새로 · 같음 · 씨앗 내용으로 맞춤.
    같은 조합에 날개형 줄이 있어도 사람형 씨앗 넣기가 사람형 줄만 본다 (11/10, 전에는 둘이 잡혀 오류)"""

    from app.db.session import engine
    from app.seed import form_stories

    combo = dict(place="숲", problem="길을 잃었어요", action="그 자리에서 기다렸어요", result="길을 찾았어요", length="long", voice_mode="narration")
    seed = [{**combo, "form": "winged", "payload": {"book": {"title": "날개"}}, "status": "approved", "model": "m",
             "prompt_version": form_stories.VERSION, "reviewed_by": "auto-check", "note": None}]
    with engine.begin() as c:
        assert form_stories.apply(c, seed) == {"new": 1, "updated": 0, "same": 0}
    with engine.begin() as c:
        assert form_stories.apply(c, seed) == {"new": 0, "updated": 0, "same": 1}
    with engine.begin() as c:
        assert form_stories.apply(c, [{**seed[0], "status": "draft", "note": "검수 지적"}]) == {"new": 0, "updated": 1, "same": 0}
    with SessionLocal() as db:
        db.add(CardStory(**combo, payload={"book": {"title": "사람"}}, status="approved"))
        db.commit()
        path = tmp_path / "seed.jsonl.gz"
        assert export_seed(db, path) == 1                      # 사람형만 (날개형은 draft 이기도 하고 형태도 다르다)
        assert load_seed(db, path) == {"new": 0, "updated": 0, "same": 1}
        assert db.query(CardStory).filter_by(form="winged").one().status == "draft"
        db.query(CardStory).delete()
        db.commit()


def test_form_seed_file():
    """씨앗 파일: 날개형 512 조합 모두 · 4족 · 사슬형은 고친 것만 approved, 이름 자리 · 형태 몸 규칙을 지나야 approved"""

    from app.seed.form_stories import KEY as FKEY
    from app.seed.form_stories import read_seed as read_form_seed

    rows = read_form_seed()
    assert len({tuple(d[k] for k in FKEY) for d in rows}) == len(rows)
    winged = [d for d in rows if d["form"] == "winged"]
    assert len(winged) == 512 and {d["status"] for d in rows if d["form"] != "winged"} == {"approved"}
    assert all(d["note"] for d in winged if d["status"] == "draft")
