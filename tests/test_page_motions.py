"""가만히 쪽을 쪽 글에 맞는 동작으로 바꾸기 · 새 동작 상한 (2026-11-04, pt_docs 17)."""

from app.services.book import ACTIONS, Book, Page
from app.services.page_motions import FALLBACK, NEW_ACTIONS, NEW_PER_STORY, cap_new, liven
from app.services.voices import NARRATOR


def _book(*pages: tuple[str, str, str]) -> Book:
    return Book(
        "t",
        [Page(i + 1, stage, action, [(NARRATOR, text, "")]) for i, (stage, action, text) in enumerate(pages)],
        [1],
    )


def test_idle_pages_follow_the_words():
    book = liven(_book(
        ("problem", "idle", "어디 있지? 열심히 찾았어요."),
        ("problem", "idle", "무서워서 덜덜 떨었어요."),
        ("result", "idle", "모두 깔깔 웃었어요."),
        ("result", "idle", "“해냈다!” 하고 외쳤어요."),
    ))
    assert [p.action for p in book.pages] == ["search", "shiver", "hooray", "hooray"]


def test_idle_without_words_breathes_and_never_stays_still():
    book = liven(_book(("place", "idle", "하늘이 맑았어요."), ("place", "idle", "바람이 불었어요.")))
    assert [p.action for p in book.pages] == ["breathe", "breathe"]


def test_sea_action_page_swims_and_walk_moods():
    book = liven(_book(
        ("action", "idle", "용기를 냈어요."),
        ("place", "walk", "씩씩하게 걸어갔어요."),
        ("place", "walk", "그냥 걸었어요."),
    ), place="바다")
    assert [p.action for p in book.pages] == ["swim", "march", "walk"]


def test_cap_keeps_the_most_used_new_motions():
    book = _book(
        ("place", "swim", "a"), ("place", "swim", "b"), ("problem", "search", "c"), ("problem", "search", "d"),
        ("action", "kick", "e"), ("action", "fly", "f"), ("result", "hooray", "g"), ("result", "breathe", "h"),
    )
    cap_new(book)
    actions = [p.action for p in book.pages]
    assert len({a for a in actions if a in FALLBACK}) == NEW_PER_STORY
    assert actions[:4] == ["swim", "swim", "search", "search"]
    assert "breathe" in actions  # 미리 렌더하는 것은 세지 않는다


def test_cap_without_render_uses_only_prerendered():
    book = cap_new(_book(("place", "swim", "a"), ("result", "hooray", "b")), can_render=False)
    assert [p.action for p in book.pages] == ["dance", "jump"]


def test_every_new_action_is_known_to_the_script():
    assert set(NEW_ACTIONS) <= set(ACTIONS)
    assert set(FALLBACK) <= set(NEW_ACTIONS)


def test_retag_rules():
    from app.seed.retag_motions import problems

    ok = ["walk", "search", "shiver", "breathe", "breathe", "hooray", "walk", "dance"]
    assert problems(ok, 8) == []
    assert problems(ok[:7], 8)                                    # 쪽 수가 다르다
    assert problems([*ok[:7], "teleport"], 8)                     # 목록에 없다
    assert problems(["walk"] * 4 + ok[4:], 8)                     # 네 쪽 연속
    assert problems(["breathe", "walk"] * 4, 8)                   # 숨쉬기가 세 쪽을 넘는다
    assert problems(["swim", "fly", "kick", "point", "walk", "run", "jump", "dance"], 8)  # 새 동작 네 가지
