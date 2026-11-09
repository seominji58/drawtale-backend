"""긴 이야기의 쪽마다 음성 · 쪽마다 동작 영상 (가짜 TTS · 가짜 AI — 외부 API 를 부르지 않는다)."""

import uuid
from types import SimpleNamespace

from app.schemas.ai import RenderResult
from app.services import jobs
from app.services.book import Book, Page, fallback_book
from app.services.storage import get_storage
from app.services.tts import ElevenLabsSpeech
from app.services.voices import NARRATOR, PROTAGONIST

CHOICES = {
    "place": "숲",
    "problem": "길을 잃었어요",
    "action": "물어봤어요",
    "result": "친구를 만났어요",
}
JOINTS = [{"name": "hip", "x": 1, "y": 2}]


class FakeTTS(ElevenLabsSpeech):
    def __init__(self, fail_on: str | None = None):
        super().__init__("k", "narrator-voice", "m")
        self.spoken, self.dialogues, self.fail_on = [], [], fail_on

    def speak(self, text):
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("tts failed")
        self.spoken.append(text)
        return b"mp3:" + text.encode()

    def dialogue(self, lines, voices):
        self.dialogues.append((lines, voices))
        return b"dialogue"


class FakeAI:
    def __init__(self):
        self.motions = []

    def render(self, req):
        self.motions.append((req.motion, req.max_frames))
        return RenderResult(
            content=b"mp4", content_type="video/mp4", processing_time_ms=1, model_version="t"
        )


def _story(**kw):
    base = dict(id=uuid.uuid4(), voice_mode="narration", protagonist_voice="girl", voice_cast=None,
                narrator_voice_id=None)
    return SimpleNamespace(**{**base, **kw})


def _character(**kw):
    base = dict(
        id="c1",
        ai_request_id="r1",
        image_width=10,
        image_height=10,
        corrections=[],
        ai_joints=JOINTS,
        form="biped",
        original_deleted_at=None,
        preview_blob_path=None,
        motion_clips=None,
    )
    return SimpleNamespace(**{**base, **kw})


def test_page_audio_one_file_per_page(monkeypatch):
    tts = FakeTTS()
    monkeypatch.setattr(jobs, "get_tts", lambda *_: tts)
    book = fallback_book(CHOICES, 8)
    story = _story()
    paths = jobs.make_page_audio(story, get_storage(), book)
    assert len(paths) == 8
    assert paths[0] == f"results/{story.id}/page01.mp3"
    assert get_storage().read(paths[2]) == b"mp3:" + book.pages[2].text().encode()
    # 쪽 글을 다 합치면 이야기 전체 글과 같다 — 쪽으로 나눠도 읽는 글자 수는 그대로
    assert "".join(sorted(tts.spoken)) == "".join(sorted(p.text() for p in book.pages))


def test_page_audio_failure_is_per_page(monkeypatch):
    book = fallback_book(CHOICES, 8)
    tts = FakeTTS(fail_on=book.pages[3].text())
    monkeypatch.setattr(jobs, "get_tts", lambda *_: tts)
    paths = jobs.make_page_audio(_story(), get_storage(), book)
    assert paths[3] is None
    assert all(p for i, p in enumerate(paths) if i != 3)


def test_page_audio_without_tts(monkeypatch):
    monkeypatch.setattr(jobs, "get_tts", lambda *_: None)
    assert jobs.make_page_audio(_story(), get_storage(), fallback_book(CHOICES, 8)) == [None] * 8


def test_character_voices_only_on_dialogue_pages(monkeypatch):
    tts = FakeTTS()
    monkeypatch.setattr(jobs, "get_tts", lambda *_: tts)
    pages = [
        Page(1, "place", "walk", [(NARRATOR, "숲에 갔어요.", "")]),
        Page(
            2,
            "problem",
            "idle",
            [(NARRATOR, "길을 잃었어요.", ""), (PROTAGONIST, "어디지?", "놀람")],
        ),
        Page(3, "action", "wave", [(NARRATOR, "물어봤어요.", ""), ("부엉이", "이쪽이야.", "다정")]),
        Page(4, "result", "dance", [(NARRATOR, "친구를 만났어요.", "")]),
    ]
    book = Book("숲", pages, [1, 2, 3, 4], "하루", {"부엉이": "작은 동물"})
    story = _story(voice_mode="characters")
    jobs.make_page_audio(story, get_storage(), book)
    assert len(tts.dialogues) == 2 and len(tts.spoken) == 2
    # 인물 → 목소리는 이야기 전체에서 한 번 정한다 (두 쪽이 같은 표를 쓴다)
    assert tts.dialogues[0][1] is tts.dialogues[1][1]
    assert set(story.voice_cast) >= {PROTAGONIST, "부엉이"}


PAGES = [
    Page(1, "place", "walk", [(NARRATOR, "a.", "")]),
    Page(2, "problem", "idle", [(NARRATOR, "b.", "")]),
    Page(3, "action", "wave", [(NARRATOR, "c.", "")]),
    Page(4, "action", "run", [(NARRATOR, "d.", "")]),
    Page(5, "result", "dance", [(NARRATOR, "e.", "")]),
]


def test_page_clips_copy_prerendered(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    storage = get_storage()
    storage.save("previews/c1-wave.mp4", b"wave")
    for m in ("walk", "run", "dab_gentle"):
        storage.save(f"previews/c1-{m}.mp4", m.encode())
    story = _story()
    character = _character(
        preview_blob_path="previews/c1-wave.mp4",
        motion_clips={m: f"previews/c1-{m}.mp4" for m in ("walk", "run", "dab_gentle")},
    )
    clips = jobs.make_page_clips(story, character, storage, Book("숲", PAGES, [1, 2, 3, 5]))
    # 미리 만든 영상을 쓰므로 렌더하지 않는다.
    # 관절을 고쳐도 이야기 영상이 남게 이야기 폴더로 복사한다
    # (사람형 왼쪽 걸음 walk_left · run_left 는 미리 만든 것이 없으면 여기서 렌더한다 — 따로 test_left_walk_clips)
    assert _base(ai.motions) == []
    # 쪽 동작마다 제 동작 (2026-11-03 — 전에는 걷기 · 뛰기 · 춤이 모두 점프였다)
    clips = {k: v for k, v in clips.items() if not k.endswith("_left")}
    assert clips == {
        m: f"results/{story.id}/{m}.mp4" for m in ("wave_hello_gentle", "walk", "run", "dab_gentle")
    }
    assert storage.read(clips["walk"]) == b"walk"


def test_page_clips_skip_failed_prerender(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    character = _character(motion_clips={"walk": None, "run": None, "dab_gentle": None})
    clips = jobs.make_page_clips(
        _story(), character, get_storage(), Book("숲", PAGES, [1, 2, 3, 5])
    )
    # 미리 렌더가 실패한 동작은 이야기를 만들 때 다시 하지 않는다 (몇 분씩 걸린다) — 멈춘 그림
    assert _base(ai.motions) == [("wave_hello_gentle", jobs.PREVIEW_MAX_FRAMES)]
    assert {k for k in clips if not k.endswith("_left")} == {"wave_hello_gentle"}


def test_page_clips_render_for_old_characters(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    clips = jobs.make_page_clips(
        _story(), _character(), get_storage(), Book("숲", PAGES, [1, 2, 3, 5])
    )
    # 미리 만든 것이 없는 예전 그림: 쪽에 나오는 동작마다 한 번
    assert sorted(m for m, _ in _base(ai.motions)) == ["dab_gentle", "run", "walk", "wave_hello_gentle"]
    assert sorted(k for k in clips if k.endswith("_left")) == ["run_left", "walk_left"]
    assert len(clips) == 6


class FakeDB:
    def __init__(self, on_refresh=None):
        self.commits, self.refreshes, self.on_refresh = 0, 0, on_refresh

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        self.refreshes += 1
        if self.on_refresh:
            self.on_refresh(obj, self.refreshes)


def test_store_previews_then_motion_clips(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    character = _character(preview_status="rendering", motion_clips={})
    db = FakeDB()
    jobs._store_previews(db, character, get_storage())
    # 미리보기를 먼저 커밋한다 (S-05 가 기다린다). 쪽 동작들은 그다음
    assert [m for m, _ in ai.motions] == ["wave_hello_gentle", *jobs.PRERENDER_MOTIONS]
    # 미리보기 · 「렌더 중」 표시 · 동작 하나 끝날 때마다 (이야기가 필요한 동작만 기다리게)
    assert character.preview_status == "ready" and db.commits == 2 + len(jobs.PRERENDER_MOTIONS)
    assert character.motion_clips["jumping_gentle"].endswith("-jumping_gentle.mp4")


def test_store_previews_discards_clips_if_original_deleted(monkeypatch):
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: FakeAI())
    character = _character(preview_status="rendering", motion_clips={})
    saved = []
    storage = get_storage()
    real_save = storage.save
    monkeypatch.setattr(storage, "save", lambda p, d: saved.append(p) or real_save(p, d))

    def original_deleted(obj, n):
        if n == 3:  # 걷기 · 점프를 만든 뒤, 렌더하는 동안 원본을 지웠다 (관절을 다시 고쳐도 마찬가지)
            obj.motion_clips = None

    jobs._store_previews(FakeDB(original_deleted), character, storage)
    assert character.motion_clips is None
    clip = next(p for p in saved if p.endswith("-jumping_gentle.mp4"))
    try:
        storage.read(clip)
        raise AssertionError("옛 관절로 만든 영상이 남았다")
    except FileNotFoundError:
        pass


def test_start_previews_returns_old_files():
    character = _character(
        preview_blob_path="previews/a.mp4",
        preview_status="ready",
        motion_clips={"jumping_gentle": "previews/b.mp4"},
    )
    # 옆에 둔 투명 프레임 묶음도 같이 지운다 (없으면 아무 일도 하지 않는다)
    assert jobs.start_previews(character) == [
        "previews/a.mp4", "previews/b.mp4", "previews/a.sprite.webp", "previews/b.sprite.webp",
        "previews/a.sprite.json", "previews/b.sprite.json",
    ]
    assert character.preview_status == "rendering" and character.motion_clips == {}


def test_story_waits_for_running_prerender(monkeypatch):
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    character = _character(motion_clips={})

    def done(obj, n):
        if n == 3:
            obj.motion_clips = {"jumping_gentle": "previews/j.mp4"}

    db = FakeDB(done)
    jobs.wait_for_motion_clips(db, character)
    assert db.refreshes == 3 and character.motion_clips["jumping_gentle"]


def test_page_clips_skip_without_session(monkeypatch):
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: FakeAI())
    book = fallback_book(CHOICES, 8)
    assert jobs.make_page_clips(_story(), _character(ai_request_id=None), get_storage(), book) == {}


def test_long_story_response_has_page_urls(client, png_bytes, monkeypatch):
    tts = FakeTTS()
    monkeypatch.setattr(jobs, "get_tts", lambda *_: tts)
    res = client.post("/api/v1/characters", files={"image": ("d.png", png_bytes, "image/png")})
    res = client.post(
        "/api/v1/stories",
        json={"character_id": res.json()["character_id"], **CHOICES, "length": "long"},
    )
    story = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert story["status"] == "succeeded"
    assert story["audio_url"] is None  # 긴 이야기는 쪽마다만 만든다
    pages = story["book"]["pages"]
    assert all(p["audio_url"].split("?")[0].endswith(f"page{p['page']:02d}.mp3") for p in pages)
    assert "audio_blob_path" not in pages[0]
    # 목 AI 는 영상을 만들지 않는다 → 쪽 영상은 없고 이야기 영상 자리에는 그림
    assert all(p["animation_url"] is None for p in pages)
    assert story["animation_url"].split("?")[0].endswith(".png")  # 주소에 서명이 붙는다 (2026-11-02)


class SpriteAI(FakeAI):
    """투명 프레임 묶음까지 주는 AI 서버 (drawtale-ai sprite=true, 2026-11-03)"""

    def render(self, req):
        self.motions.append((req.motion, req.max_frames))
        assert req.sprite  # 백엔드는 늘 묶음도 달라고 한다
        return RenderResult(content=b"mp4", sprite=b"webp:" + req.motion.encode(), sprite_frames=15,
                            sprite_box=[0.2, 0.1, 0.8, 0.9])


def test_sprite_saved_next_to_clip(monkeypatch):
    ai = SpriteAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    storage = get_storage()
    character = _character(preview_status="rendering", motion_clips={})
    jobs._store_previews(FakeDB(), character, storage)
    walk = character.motion_clips["walk"]
    # 영상 옆 같은 이름 — 따로 칸을 두지 않는다
    assert storage.read(jobs.sprite_path_for(walk)) == b"webp:walk"
    # 장 수 · 그림 자리는 서버가 준다 (화면은 다른 주소 그림의 픽셀을 못 읽는다)
    assert jobs.read_sprite_meta(storage, walk) == {"frames": 15, "box": [0.2, 0.1, 0.8, 0.9]}
    preview = jobs.sprite_path_for(character.preview_blob_path)
    assert storage.read(preview) == b"webp:wave_hello_gentle"


def test_page_clips_copy_sprites_only_when_present(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    storage = get_storage()
    storage.save("previews/s1-walk.mp4", b"walk")
    storage.save("previews/s1-walk.sprite.webp", b"walk-sprite")
    storage.save("previews/s1-walk.sprite.json", b'{"frames": 12, "box": [0, 0, 1, 1]}')
    storage.save("previews/s1-run.mp4", b"run")   # 예전 AI 가 만든 영상: 묶음 없음
    story = _story()
    character = _character(preview_blob_path=None, motion_clips={"walk": "previews/s1-walk.mp4",
                                                                 "run": "previews/s1-run.mp4"})
    sprites: set[str] = set()
    book = Book("숲", PAGES, [1, 2, 3, 5])
    clips = jobs.make_page_clips(story, character, storage, book, sprites)
    assert storage.read(jobs.sprite_path_for(clips["walk"])) == b"walk-sprite"
    assert jobs.read_sprite_meta(storage, clips["walk"])["frames"] == 12
    # 묶음이 실제로 있는 동작만 — 없는 쪽은 화면이 영상으로 보여 준다
    assert "walk" in sprites and "run" not in sprites


def test_book_response_has_sprite_url():
    from app.api.v1.stories import book_response

    storage = get_storage()
    book = {"pages": [
        {"page": 1, "animation_blob_path": "results/x/walk.mp4",
         "sprite_blob_path": "results/x/walk.sprite.webp"},
        {"page": 2, "animation_blob_path": None},
    ]}
    pages = book_response(book, storage)["pages"]
    assert pages[0]["sprite_url"] and "walk.sprite.webp" in pages[0]["sprite_url"]
    assert pages[1]["sprite_url"] is None
    assert "sprite_blob_path" not in pages[0]


def test_book_schema_keeps_sprite_and_scene():
    """응답 스키마가 화면 장면 층에 필요한 값을 버리지 않는다 (2026-11-03 — 빠져서 장면 층이 안 켜졌음)."""
    from app.api.v1.stories import book_response
    from app.schemas.story import Book

    storage = get_storage()
    book = {"title": "t", "order_pages": [1], "character_sheet": {"친구": "a girl"},
            "pages": [{"page": 1, "stage": "place", "action": "walk", "text": "a", "lines": [],
                       "sprite_blob_path": "results/x/walk.sprite.webp",
                       "sprite_frames": 36, "sprite_box": [0.1, 0.2, 0.8, 0.9],
                       "scene": {"hero_on_page": True, "characters": [{"name": "친구", "slot": "right-middle"}],
                                 "background": "b", "location": "school-hallway"}}]}
    out = Book.model_validate(book_response(book, storage)).model_dump()
    p = out["pages"][0]
    assert p["sprite_frames"] == 36 and p["sprite_box"] == [0.1, 0.2, 0.8, 0.9]
    assert p["scene"]["location"] == "school-hallway"
    assert p["scene"]["characters"][0]["name"] == "친구"
    assert out["character_sheet"] == {"친구": "a girl"}


class InPlaceAI(SpriteAI):
    """걷기 · 뛰기를 제자리로 렌더하는 AI (drawtale-ai 11/3 저녁)"""

    def render(self, req):
        r = super().render(req)
        r.sprite_in_place = True
        return r


def test_old_travel_sprite_rerendered_once(monkeypatch):
    """예전 AI 의 걷기 묶음(칸 밖으로 흘러가 잘림)은 이야기 만들 때 다시 렌더해 캐릭터 쪽도 바꾼다"""
    ai = InPlaceAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    storage = get_storage()
    for m in ("walk", "dab_gentle"):
        storage.save(f"previews/s2-{m}.mp4", b"old")
        storage.save(f"previews/s2-{m}.sprite.webp", b"old-sprite")
        storage.save(f"previews/s2-{m}.sprite.json", b'{"frames": 12, "box": [0, 0, 1, 1]}')
    character = _character(preview_blob_path=None, motion_clips={"walk": "previews/s2-walk.mp4",
                                                                 "dab_gentle": "previews/s2-dab_gentle.mp4"})
    pages = [Page(1, "place", "walk", [(NARRATOR, "a", "")]), Page(2, "result", "dance", [(NARRATOR, "b", "")])]
    sprites: set[str] = set()
    clips = jobs.make_page_clips(_story(), character, storage, Book("t", pages, [1, 2]), sprites)
    # 걷기만 다시 (춤은 제자리 동작이라 예전 것 그대로)
    assert [m for m, _ in _base(ai.motions)] == ["walk"]
    assert storage.read(jobs.sprite_path_for(clips["walk"])) == b"webp:walk"
    assert storage.read("previews/s2-walk.sprite.webp") == b"webp:walk"
    assert storage.read(jobs.sprite_path_for(clips["dab_gentle"])) == b"old-sprite"
    # 다음 이야기는 복사만
    ai.motions.clear()
    jobs.make_page_clips(_story(), character, storage, Book("t", pages, [1, 2]), set())
    assert _base(ai.motions) == []


def test_old_ai_rerender_tried_once(monkeypatch):
    """AI 가 아직 예전 판(제자리 표시 없음)이면 한 번만 시도하고 이야기마다 되풀이하지 않는다"""
    ai = SpriteAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    storage = get_storage()
    storage.save("previews/s3-walk.mp4", b"old")
    storage.save("previews/s3-walk.sprite.webp", b"old-sprite")
    storage.save("previews/s3-walk.sprite.json", b'{"frames": 12, "box": [0, 0, 1, 1]}')
    character = _character(preview_blob_path=None, motion_clips={"walk": "previews/s3-walk.mp4"})
    pages = [Page(1, "place", "walk", [(NARRATOR, "a", "")])]
    clips = jobs.make_page_clips(_story(), character, storage, Book("t", pages, [1]), set())
    assert [m for m, _ in _base(ai.motions)] == ["walk"]
    assert storage.read(jobs.sprite_path_for(clips["walk"])) == b"old-sprite"
    ai.motions.clear()
    jobs.make_page_clips(_story(), character, storage, Book("t", pages, [1]), set())
    assert _base(ai.motions) == []


def test_short_story_copies_prerendered_clip_with_sprite(monkeypatch):
    """짧은 이야기도 미리 만든 동작(과 투명 프레임 묶음)을 복사한다 — 화면이 장면 층으로 (2026-11-03)."""
    ai = InPlaceAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    storage = get_storage()
    storage.save("previews/s4-run.mp4", b"run")
    storage.save("previews/s4-run.sprite.webp", b"run-sprite")
    storage.save("previews/s4-run.sprite.json", b'{"frames": 36, "box": [0.1, 0.2, 0.8, 0.9], "in_place": true}')
    story = _story(action="밝은 곳으로 달려갔어요")
    path = jobs.render_story(story, _character(motion_clips={"run": "previews/s4-run.mp4"}), storage)
    assert ai.motions == []   # 렌더하지 않고 복사
    assert path.endswith("/run.mp4")
    assert storage.read(jobs.sprite_path_for(path)) == b"run-sprite"
    assert jobs.read_sprite_meta(storage, path)["frames"] == 36
    # 미리 만든 것이 없으면 렌더하고 묶음도 옆에 둔다
    story2 = _story(action="친구와 춤을 췄어요")
    path2 = jobs.render_story(story2, _character(motion_clips={}), storage)
    assert [m for m, _ in ai.motions] == ["dab_gentle"]
    assert storage.read(jobs.sprite_path_for(path2)) == b"webp:dab_gentle"
    assert jobs.MOTION_ACTION[jobs.motion_for(story2.action)] == "dance"



def test_story_waits_only_for_needed_motions(monkeypatch):
    """미리 렌더가 동작마다 커밋되므로, 이야기는 자기에게 필요한 동작만 끝나면 시작한다 (2026-11-03)."""
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    character = _character(motion_clips={"walk": "previews/w.mp4", "jumping_gentle": jobs.PENDING,
                                         "run": jobs.PENDING, "dab_gentle": jobs.PENDING})
    db = FakeDB()
    jobs.wait_for_motion_clips(db, character, {"walk"})
    assert db.refreshes == 0   # 걷기만 필요 — 바로
    def jump_done(obj, n):
        if n == 2:
            obj.motion_clips = {**obj.motion_clips, "jumping_gentle": "previews/j.mp4"}
    db = FakeDB(jump_done)
    jobs.wait_for_motion_clips(db, character, {"walk", "jumping_gentle"})
    assert db.refreshes == 2 and character.motion_clips["run"] == jobs.PENDING


def test_page_audio_retries_before_giving_up(monkeypatch):
    """쪽 음성이 한두 번 실패해도 다시 만든다 — 실패한 쪽에 브라우저 기계 음성이 끼지 않게 (2026-11-03)."""
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Flaky:
        def speak(self, text):
            calls["n"] += 1
            if calls["n"] % 3 != 0:   # 쪽마다 두 번 실패하고 세 번째에 된다
                raise RuntimeError("429")
            return b"ID3"

    monkeypatch.setattr(jobs, "get_tts", lambda voice=None: Flaky())
    monkeypatch.setattr(jobs, "PAGE_AUDIO_WORKERS", 1)
    book = Book("숲", PAGES, [1, 2, 3, 5])
    paths = jobs.make_page_audio(_story(), get_storage(), book)
    assert all(paths) and calls["n"] == 3 * len(PAGES)


def test_story_still_is_trimmed_to_drawing():
    """이야기 정지 그림은 그림 자리만 — 둘레 여백이 있으면 멈춘 쪽 주인공이 작아졌다 (2026-11-03)."""
    import io

    from PIL import Image

    im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    im.paste((200, 50, 50, 255), (100, 150, 300, 450))
    buf = io.BytesIO(); im.save(buf, format="PNG")
    out = Image.open(io.BytesIO(jobs.trim_still(buf.getvalue())))
    assert out.size[0] <= 210 and out.size[1] <= 310 and out.size[0] >= 200
    assert jobs.trim_still(b"not png") == b"not png"


def _base(motions):
    """사람형 왼쪽 걸음(*_left)을 뺀 렌더 요청"""
    return [m for m in motions if not (m[0] if isinstance(m, tuple) else m).endswith("_left")]


def test_left_walk_clips_only_for_humans():
    """사람형 걷기 · 뛰기 쪽만 왼쪽 걸음을 같이 (11/9 — 왼쪽으로 갈 때 그림을 뒤집지 않으려고)"""
    pages = [Page(1, "place", "walk", [(NARRATOR, "a", "")]), Page(2, "action", "run", [(NARRATOR, "b", "")]),
             Page(3, "result", "dance", [(NARRATOR, "c", "")])]
    book = Book("t", pages, [1, 2, 3])
    assert jobs.left_motions(_character(), book) == {"walk_left", "run_left"}
    assert jobs.left_motions(_character(form="quadruped"), book) == set()
