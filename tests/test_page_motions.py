"""가만히 쪽을 쪽 글에 맞는 동작으로 바꾸기 · 새 동작 상한 (2026-11-04, pt_docs 17)."""

import pytest

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


@pytest.mark.parametrize(
    "text,action",
    [
        # 아닌 말은 기분 동작으로 고르지 않는다 (11/6 이야기 15편 점검: 「안 무서워요」 쪽이 떨기였다)
        ("“이제 하나도 안 무서워요!”", "breathe"),
        ("하나도 안무서웠어요.", "breathe"),
        ("이제 춥지 않았어요.", "breathe"),
        ("무서운 마음이 어느새 사라졌어요.", "breathe"),
        ("웃음이 나지 않았어요.", "breathe"),
        ("친구는 안 웃었어요.", "breathe"),
        ("웃음이 쏙 사라져 버렸어요.", "breathe"),
        # 아닌 말이 아니면 그대로
        ("무서워서 덜덜 떨었어요.", "shiver"),
        ("너무 추워서 오들오들했어요.", "shiver"),
        ("모두 깔깔 웃었어요.", "hooray"),
        ("나는 웃음이 났어요.", "hooray"),
        # 한 문장은 아닌 말이어도 다른 낱말 · 다른 문장이 맞으면 그 동작
        ("안 무섭다고 했지만 덜덜 떨었어요.", "shiver"),
        ("무서움이 사라졌어요. 우리는 깔깔 웃었어요.", "hooray"),
        ("이제 안 무서워요. 그래도 몸이 덜덜 떨렸어요.", "shiver"),
        # 몸짓 낱말은 「못 ~」 이어도 그 몸짓 (찾는 몸짓은 맞다)
        ("공을 끝내 못 찾았어요.", "search"),
        ("찾던 공이 사라졌어요. 두리번두리번 살폈어요.", "search"),
    ],
)
def test_negated_mood_words_are_not_chosen(text, action):
    assert liven(_book(("result", "idle", text))).pages[0].action == action


@pytest.mark.parametrize("text", ["한 걸음, 또 한 걸음. 천천히 해 보았어요.", "친구를 따라갔어요.", "숲길을 뚜벅뚜벅 걸었어요."])
def test_walking_words_make_idle_page_walk(text):
    """걷는 글인데 가만히 쪽이면 제자리에 서 있었다 (11/9 테스트 2번) — 걷기로, 장면이 옮겨 간다"""
    assert liven(_book(("action", "idle", text))).pages[0].action == "walk"


def test_stopped_walking_is_not_walk():
    # 11/12 사용자: 「걱정이 되어 걸음을 멈췄어요」가 걷기라 주인공이 크게 깨졌다
    pages = liven(_book(("problem", "idle", "토토는 걱정이 되어 걸음을 멈췄어요."),
                        ("action", "idle", "한 걸음, 또 한 걸음 걸어갔어요."),
                        ("action", "idle", "토토는 걸음을 멈추고 친구에게 다가갔어요."))).pages
    assert [p.action for p in pages] == ["breathe", "walk", "walk"]
