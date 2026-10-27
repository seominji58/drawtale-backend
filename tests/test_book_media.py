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
    base = dict(id=uuid.uuid4(), voice_mode="narration", protagonist_voice="girl", voice_cast=None)
    return SimpleNamespace(**{**base, **kw})


def _character(**kw):
    base = dict(
        id="c1",
        ai_request_id="r1",
        image_width=10,
        image_height=10,
        corrections=[],
        ai_joints=JOINTS,
        original_deleted_at=None,
        preview_blob_path=None,
        motion_clips=None,
    )
    return SimpleNamespace(**{**base, **kw})


def test_page_audio_one_file_per_page(monkeypatch):
    tts = FakeTTS()
    monkeypatch.setattr(jobs, "get_tts", lambda: tts)
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
    monkeypatch.setattr(jobs, "get_tts", lambda: tts)
    paths = jobs.make_page_audio(_story(), get_storage(), book)
    assert paths[3] is None
    assert all(p for i, p in enumerate(paths) if i != 3)


def test_page_audio_without_tts(monkeypatch):
    monkeypatch.setattr(jobs, "get_tts", lambda: None)
    assert jobs.make_page_audio(_story(), get_storage(), fallback_book(CHOICES, 8)) == [None] * 8


def test_character_voices_only_on_dialogue_pages(monkeypatch):
    tts = FakeTTS()
    monkeypatch.setattr(jobs, "get_tts", lambda: tts)
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
    storage.save("previews/c1-jump.mp4", b"jump")
    story = _story()
    character = _character(
        preview_blob_path="previews/c1-wave.mp4",
        motion_clips={"jumping_gentle": "previews/c1-jump.mp4"},
    )
    clips = jobs.make_page_clips(story, character, storage, Book("숲", PAGES, [1, 2, 3, 5]))
    # 미리 만든 영상을 쓰므로 렌더하지 않는다.
    # 관절을 고쳐도 이야기 영상이 남게 이야기 폴더로 복사한다
    assert ai.motions == []
    assert clips == {
        "wave_hello_gentle": f"results/{story.id}/wave_hello_gentle.mp4",
        "jumping_gentle": f"results/{story.id}/jumping_gentle.mp4",
    }
    assert storage.read(clips["jumping_gentle"]) == b"jump"


def test_page_clips_skip_failed_prerender(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    character = _character(motion_clips={"jumping_gentle": None})
    clips = jobs.make_page_clips(
        _story(), character, get_storage(), Book("숲", PAGES, [1, 2, 3, 5])
    )
    # 미리 렌더가 실패한 동작은 이야기를 만들 때 다시 하지 않는다 (몇 분씩 걸린다) — 멈춘 그림
    assert ai.motions == [("wave_hello_gentle", jobs.PREVIEW_MAX_FRAMES)]
    assert set(clips) == {"wave_hello_gentle"}


def test_page_clips_render_for_old_characters(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    clips = jobs.make_page_clips(
        _story(), _character(), get_storage(), Book("숲", PAGES, [1, 2, 3, 5])
    )
    # 미리 만든 것이 없는 예전 그림: 동작마다 한 번 (walk · run · dance 가 같은 동작)
    assert sorted(ai.motions) == [
        ("jumping_gentle", jobs.PREVIEW_MAX_FRAMES),
        ("wave_hello_gentle", jobs.PREVIEW_MAX_FRAMES),
    ]
    assert len(clips) == 2


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
    # 미리보기를 먼저 커밋한다 (S-05 가 기다린다). 통통 뛰기는 그다음
    assert [m for m, _ in ai.motions] == ["wave_hello_gentle", "jumping_gentle"]
    assert character.preview_status == "ready" and db.commits == 2
    assert character.motion_clips["jumping_gentle"].endswith("-jumping_gentle.mp4")


def test_store_previews_discards_clips_if_original_deleted(monkeypatch):
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: FakeAI())
    character = _character(preview_status="rendering", motion_clips={})
    saved = []
    storage = get_storage()
    real_save = storage.save
    monkeypatch.setattr(storage, "save", lambda p, d: saved.append(p) or real_save(p, d))

    def original_deleted(obj, n):
        obj.motion_clips = None  # 렌더하는 동안 원본을 지웠다 (관절을 다시 고쳐도 마찬가지)

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
    assert jobs.start_previews(character) == ["previews/a.mp4", "previews/b.mp4"]
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
    monkeypatch.setattr(jobs, "get_tts", lambda: tts)
    res = client.post("/api/v1/characters", files={"image": ("d.png", png_bytes, "image/png")})
    res = client.post(
        "/api/v1/stories",
        json={"character_id": res.json()["character_id"], **CHOICES, "length": "long"},
    )
    story = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert story["status"] == "succeeded"
    assert story["audio_url"] is None  # 긴 이야기는 쪽마다만 만든다
    pages = story["book"]["pages"]
    assert all(p["audio_url"].endswith(f"page{p['page']:02d}.mp3") for p in pages)
    assert "audio_blob_path" not in pages[0]
    # 목 AI 는 영상을 만들지 않는다 → 쪽 영상은 없고 이야기 영상 자리에는 그림
    assert all(p["animation_url"] is None for p in pages)
    assert story["animation_url"].endswith(".png")
