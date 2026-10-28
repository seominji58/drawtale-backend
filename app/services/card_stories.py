"""카드 이야기는 미리 만든다 (pt_docs 13 1-1, 2026-10-27 · 카드표 05 S-07 2026-10-28 확정).

카드만으로 만든 이야기는 서비스가 돌 때 만들지 않는다. 조합 512가지(장소 8 × 문제 4 × 행동 4 × 결과 4)를
길이(short · long) × 읽어주기(narration · characters)마다 미리 만들어 **사람이 한 편씩 읽고** 통과시킨 것만
card_stories 표에 둔다. 아이가 네 칸을 카드로만 고르면 꺼내 줄 뿐이다.
아이가 말로 덧붙였거나(spoken) 더 고른 칸(extras)이 있으면 지금처럼 그때 만든다.

주인공 이름 (캐릭터 목소리 판):
만들 때 자리 이름 「라라」로 쓰게 하고, 그 이름과 뒤 조사를 `{이름}{조사:가}` 처럼 바꿔 둔다.
보여 줄 때 아이가 지은 이름을 끼우고 조사를 받침에 맞춘다 (`fill_name`).
나레이션 판은 주인공이 「나」라서 이름 자리가 없다.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from app.services.book import Book, Page, Scene

# ── 카드표 (pt_docs 05 S-07 「카드」, 2026-10-28 a7eb2b2). 프론트 features/story/choices.ts 와 같은 글자 ──
PLACES = ["집", "놀이터", "학교", "마을", "숲", "바다", "우주", "동굴"]
PROBLEMS = ["넘어졌어요", "친구가 울고 있었어요", "길을 잃었어요", "어두워졌어요"]
ACTIONS = {
    "넘어졌어요": ["툭툭 털고 달려갔어요", "어른을 불렀어요", "천천히 다시 걸었어요", "잠깐 쉬며 기다렸어요"],
    "친구가 울고 있었어요": ["다가가서 도와줬어요", "손을 흔들어 웃게 했어요", "어른에게 달려갔어요", "옆에서 기다려 줬어요"],
    "길을 잃었어요": ["그 자리에서 기다렸어요", "어른에게 물어봤어요", "길을 찾아 나섰어요", "큰 길로 달려갔어요"],
    "어두워졌어요": ["숨었어요", "밝은 곳으로 달려갔어요", "살금살금 걸어갔어요", "가만히 기다렸어요"],
}
RESULTS = {
    "넘어졌어요": ["다시 씩씩해졌어요", "금방 괜찮아졌어요", "웃음이 났어요", "다시 신나게 놀았어요"],
    "친구가 울고 있었어요": ["친구가 웃었어요", "함께 놀았어요", "친한 친구가 됐어요", "모두 기뻐했어요"],
    "길을 잃었어요": ["길을 찾았어요", "무사히 돌아왔어요", "마음이 놓였어요", "모두 기뻐했어요"],
    "어두워졌어요": ["해가 떴어요", "용기가 생겼어요", "잠이 들었어요", "무사히 돌아왔어요"],
}

LENGTHS = ("short", "long")
VOICE_MODES = ("narration", "characters")

# 만들 때 쓰는 자리 이름. 받침이 없고 이야기에 다른 뜻으로 나올 일이 거의 없는 말
SLOT_NAME = "라라"
# 이름을 짓지 않은 아이에게 쓰는 이름 (book_prompt 의 기본 이름과 같다)
DEFAULT_NAME = "토토"


@dataclass(frozen=True)
class Combo:
    place: str
    problem: str
    action: str
    result: str

    def key(self) -> str:
        return " / ".join((self.place, self.problem, self.action, self.result))


def combos() -> list[Combo]:
    """512가지. 장소 → 문제 → 행동 → 결과 순서 (카드표 순서 그대로)."""

    return [
        Combo(pl, pr, ac, rs)
        for pl, pr in itertools.product(PLACES, PROBLEMS)
        for ac, rs in itertools.product(ACTIONS[pr], RESULTS[pr])
    ]


def is_card_combo(place: str, problem: str, action: str, result: str) -> bool:
    return (
        place in PLACES
        and problem in PROBLEMS
        and action in ACTIONS[problem]
        and result in RESULTS[problem]
    )


# ── 이름 자리 ──
# 자리 이름 뒤에 오는 조사 중 받침에 따라 바뀌는 것. (받침 없을 때, 받침 있을 때)
JOSA = {
    "가": "이", "는": "은", "를": "을", "와": "과", "랑": "이랑", "야": "아",
    "로": "으로", "예요": "이에요", "여": "이여", "라고": "이라고", "나": "이나",
}
_SLOT = re.compile(SLOT_NAME + "(" + "|".join(sorted(JOSA, key=len, reverse=True)) + ")?")
_TOKEN = re.compile(r"\{이름\}(?:\{조사:([^}]+)\})?")


def templatize(text: str) -> str:
    """「라라가 웃었어요」 → 「{이름}{조사:가} 웃었어요」."""

    return _SLOT.sub(lambda m: "{이름}" + (f"{{조사:{m.group(1)}}}" if m.group(1) else ""), text)


def has_final(word: str) -> bool:
    """마지막 글자에 받침이 있나. 한글이 아니면 없다고 본다."""

    if not word:
        return False
    c = ord(word[-1])
    return 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 != 0


def _final_is_rieul(word: str) -> bool:
    c = ord(word[-1]) if word else 0
    return 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 == 8


def fill_name(text: str, name: str | None) -> str:
    """`{이름}{조사:가}` 를 아이가 지은 이름과 받침에 맞는 조사로 바꾼다."""

    name = (name or DEFAULT_NAME).strip() or DEFAULT_NAME
    final = has_final(name)

    def one(m: re.Match) -> str:
        josa = m.group(1)
        if not josa:
            return name
        if not final:
            return name + josa
        if josa == "로" and _final_is_rieul(name):
            return name + "로"  # 「별」 → 「별로」
        return name + JOSA.get(josa, josa)

    return _TOKEN.sub(one, text)


# ── 저장 모양 (card_stories.payload) ──
def book_payload(book: Book) -> dict:
    """긴 이야기를 저장할 모양. 글자 속 자리 이름은 이름 자리로 바꾼다."""

    data = book.to_json()
    data["title"] = templatize(data["title"])
    for p in data["pages"]:
        p["text"] = templatize(p["text"])
        for ln in p["lines"]:
            ln["text"] = templatize(ln["text"])
    return {"book": data, "cast": dict(book.cast)}


def book_from_payload(payload: dict, name: str | None) -> Book:
    """저장본 → Book. 이름을 끼운다. 쪽 음성 · 동작 영상은 부르는 쪽이 이 Book 으로 만든다."""

    data = payload["book"]
    pages = []
    for p in data["pages"]:
        sc = p.get("scene")
        pages.append(
            Page(
                p["page"],
                p["stage"],
                p["action"],
                [(ln["speaker"], fill_name(ln["text"], name), ln.get("emotion") or "") for ln in p["lines"]],
                p.get("child_words") or "",
                sc
                and Scene(
                    bool(sc.get("hero_on_page")),
                    [(c["name"], c["slot"]) for c in sc.get("characters") or []],
                    sc.get("background") or "",
                ),
            )
        )
    return Book(
        fill_name(data["title"], name),
        pages,
        list(data["order_pages"]),
        "" if not name else name,
        dict(payload.get("cast") or {}),
        dict(data.get("character_sheet") or {}),
    )


def short_payload(text: str | None = None, sentences=None, cast: dict | None = None) -> dict:
    """짧은 이야기. 나레이션 판은 글 하나, 캐릭터 목소리 판은 화자 · 감정이 붙은 문장들."""

    if sentences is None:
        return {"text": text or ""}
    return {
        "sentences": [[[s, templatize(t), e] for s, t, e in parts] for parts in sentences],
        "cast": dict(cast or {}),
    }


# ── 꺼내 주기 ──
def find(db, story) -> "CardStory | None":
    """이 이야기에 쓸 저장본. 카드로만 고른 이야기이고 사람이 통과시킨 것이 있을 때만.

    아이가 말로 덧붙였거나(spoken) 더 고른 칸(extras)이 있으면 None — 그때 만든다.
    """

    from app.models.card_story import CardStory

    if story.spoken or story.extras:
        return None
    if not is_card_combo(story.place, story.problem, story.action, story.result):
        return None
    return (
        db.query(CardStory)
        .filter_by(
            place=story.place,
            problem=story.problem,
            action=story.action,
            result=story.result,
            length=story.length,
            voice_mode=story.voice_mode,
            status="approved",
        )
        .one_or_none()
    )


def hero_name(story) -> str | None:
    """캐릭터 목소리 판이면 끼울 이름 (짓지 않았으면 기본 이름). 나레이션 판은 「나」라서 None."""

    if story.voice_mode != "characters":
        return None
    return (story.protagonist_name or "").strip() or DEFAULT_NAME
