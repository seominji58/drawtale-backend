"""쪽 동작을 늘린다 — 가만히 쪽을 쪽 글에 맞는 동작으로 (2026-11-04, pt_docs 17).

카드 이야기 1,024편(8,192쪽) 중 45%가 가만히(idle)였다. 렌더하지 않아 멈춘 그림이 된다.
CMU 동작 7개 + 관절 각도로 만든 2개를 AI 에 더했고(drawtale-ai runtime/ai_server/motions),
여기서 쪽 글 · 단계 · 장소를 보고 고른다. 카드 이야기는 다시 만들지 않고 불러올 때 바꾼다.

새 동작은 캐릭터를 만들 때 미리 렌더하지 않는다(숨쉬기만). 이야기에 나올 때 렌더하므로 이야기 하나에
NEW_PER_STORY 개까지만 쓰고, 넘는 쪽은 FALLBACK 의 미리 렌더한 동작으로 돌린다.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.book import Book

# 쪽 동작 이름 → 무엇인지. 이름은 AI 동작 이름과 같다 (기존 여섯 개는 jobs.PAGE_MOTIONS 가 따로 잇는다).
# 평면 그림에서 18개가 통과했지만 비슷해 보이는 것이 많아(같은 팔 휘젓기 · 같은 제자리 걸음) 모습마다 하나씩 9개만 둔다
NEW_ACTIONS: dict[str, str] = {
    "breathe": "제자리에서 숨 쉬기",
    "shiver": "덜덜 떨기",
    "hooray": "만세",
    "point": "손가락으로 가리키기",
    "fly": "새처럼 날기",
    "swim": "헤엄치기",
    "march": "씩씩하게 행진",
    "kick": "공 차기",
    "search": "몸을 숙여 땅을 살피기",
}

# 캐릭터를 만들 때 미리 렌더하는 새 동작. 가만히 쪽이 거의 모든 이야기에 나와서
PRERENDERED_NEW = ("breathe",)
# 이야기 하나에서 새로 렌더할 동작 수 (렌더 하나 10초 안팎, 한 번에 하나씩)
NEW_PER_STORY = 3
# 상한을 넘거나 렌더할 수 없을 때 대신 쓸 동작 — 모두 미리 렌더하는 것
FALLBACK: dict[str, str] = {
    "shiver": "breathe", "kick": "breathe", "search": "breathe",
    "hooray": "jump",
    "march": "walk",
    "point": "wave", "fly": "wave",
    "swim": "dance",
}

# 쪽 글 낱말 → 동작. 위에서부터 먼저 맞는 것. 가만히 쪽에만 쓴다.
# 기쁨 · 웃음 · 손뼉은 만세, 손을 뻗는 것은 가리키기로 — 따로 만든 동작이 평면에서 그것들과 같아 보였다
WORDS: list[tuple[tuple[str, ...], str]] = [
    (("헤엄", "수영", "물장구"), "swim"),
    (("날아", "날개", "훨훨", "파닥"), "fly"),
    (("공을 차", "공을 뻥", "슛"), "kick"),
    (("빙글", "뱅글", "빙그르"), "hooray"),   # 물고기 빙글 돌기는 만세 칸 (11/5). 사람은 만세
    (("만세", "해냈", "성공", "손뼉", "박수", "짝짝", "깔깔", "웃었", "웃음", "하하", "히히",
      "신나", "신났", "기뻐", "기뻤", "기쁘", "야호", "좋아했"), "hooray"),
    (("덜덜", "무서", "떨렸", "떨었", "추워", "춥", "오들오들", "돌돌", "똬리"), "shiver"),
    (("찾", "어디 있", "어디에", "두리번", "살펴", "주웠", "줍", "킁킁", "냄새", "고개를 들"), "search"),
    (("가리켰", "저기", "저쪽", "저것", "손을 뻗", "까치발"), "point"),
]
# 걷기 쪽은 걸음에 씩씩함이 드러나면 행진으로
WALK_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("씩씩", "행진", "하나 둘"), "march"),
]
# 장소가 바다면 행동 단계의 가만히 쪽은 헤엄
PLACE_ACTION = {"sea": "swim", "바다": "swim"}


def _match(text: str, table: list[tuple[tuple[str, ...], str]]) -> str | None:
    for words, action in table:
        if any(w in text for w in words):
            return action
    return None


def liven(book: Book, place: str | None = None) -> Book:
    """가만히 · 걷기 쪽을 쪽 글에 맞는 동작으로 바꾼다 (제자리에서 바꿈, 같은 book 을 돌려준다).

    가만히 쪽은 맞는 낱말이 없어도 숨쉬기로 — 멈춘 그림을 남기지 않는다."""

    sea = PLACE_ACTION.get((place or "").strip())
    for p in book.pages:
        text = p.text()
        if p.action == "idle":
            p.action = _match(text, WORDS) or (sea if sea and p.stage == "action" else None) or "breathe"
        elif p.action == "walk":
            p.action = _match(text, WALK_WORDS) or "walk"
    return book


def cap_new(book: Book, can_render: bool = True) -> Book:
    """미리 렌더하지 않는 새 동작을 NEW_PER_STORY 개까지만 남긴다. 쪽이 많은 것부터.

    can_render 가 아니면(원본을 지웠거나 목 AI) 새로 렌더할 수 없으니 전부 대신 동작으로."""

    fresh = Counter(p.action for p in book.pages if p.action in FALLBACK)
    keep = {a for a, _ in fresh.most_common(NEW_PER_STORY)} if can_render else set()
    for p in book.pages:
        if p.action in FALLBACK and p.action not in keep:
            p.action = FALLBACK[p.action]
    return book
