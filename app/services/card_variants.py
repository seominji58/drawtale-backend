"""미리 만드는 카드 이야기의 나머지 세 판을 기본판에서 고쳐 쓴다 (2026-10-28 사용자 「기본 설정 베이스로 나머지 제작」).

기본판 = 긴 판 · 나레이션(앱 기본 설정). 같은 조합이면 어떤 설정으로 봐도 **같은 이야기**가 되도록
나머지는 새로 쓰지 않고 기본판을 고쳐 쓴다. 생각을 적게(effort low) 하고 배경 계획은 다시 받지 않아 토큰도 준다.

    긴 판 · 캐릭터 목소리   글만 3인칭 + 이름 자리 + 인물 대사로. 쪽 배치 · 동작 · 배경 계획 · 순서 맞추기 쪽은 기본판 그대로
    짧은 판 · 나레이션      기본판 줄거리를 네 문장으로
    짧은 판 · 캐릭터 목소리 같은 줄거리를 이름 + 대사가 든 네 문장으로

검사는 그때 만드는 이야기와 같다 — 카드 글자 · 쪽 배치 · 네 문장 · 대사 · 내용 검사. 어긋나면 세 번까지 다시 쓴다.
"""

from __future__ import annotations

import json
import logging

from app.services import card_stories as cs
from app.services.book import DIALOGUE, STAGE_KO, STAGES, book_problems, mentions, parse_book
from app.services.story_writer import (
    SENTENCE_COUNT,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    count_sentences,
    has_dialogue,
    parse_script,
    script_prompt,
    script_text,
)
from app.services.voices import EMOTION_TAGS, NARRATOR, PROTAGONIST, VOICE_TYPES

log = logging.getLogger(__name__)
TRIES = 3

KEEP = """
[고쳐 쓰기 — 가장 중요]
아래 「기본판」은 사람이 읽을 같은 이야기의 다른 판이다. **줄거리 · 장면 · 순서를 그대로 둔다.**
새 사건 · 새 인물 · 새 장소를 더하지 않고, 기본판에 있는 것을 빼지 않는다.
카드 글자는 기본판처럼 그대로 남긴다.
넘어지는 장면은 가볍게 쓴다 (피 · 상처 · 멍 · 아파서 우는 모습 · 「다쳤다」를 쓰지 않는다).
"""

CHARACTERS_SYSTEM = (
    """너는 그림책 대본을 「캐릭터 목소리 판」으로 고쳐 쓰는 도우미야.
기본판은 주인공이 「나」이고 나레이터 혼자 읽는다. 이것을 같은 이야기 그대로 고쳐 쓴다.
"""
    + KEEP
    + """
[바꾸는 것]
- 주인공 이름은 「{hero}」이다. 나레이션은 이야기 밖에서 들려주는 사람이라 주인공을 「{hero}」(이)라고 부른다.
  「나」라고 쓰지 않는다. 이름 뒤 조사는 받침에 맞춘다 ({hero}는 · {hero}가 · {hero}와).
- 쪽 수와 쪽 번호는 기본판과 같다. 쪽마다 그 쪽의 내용만 옮긴다.
- 기본판에서 “ ” 안에 있던 말은 그 말을 한 인물의 대사로 뗀다. 대사가 없는 쪽에 억지로 넣지 않는다.
- 인물 이름은 기본판에 나온 그대로 쓴다 (친구, 어른). 새 이름을 짓지 않는다.
"""
    + DIALOGUE
    + """JSON 으로만 답한다.
{{"title": "기본판 제목에서 「나」만 바꾼 제목",
 "protagonist": "{hero}",
 "characters": [{{"name": "친구", "type": "아이-남"}}],
 "pages": [{{"page": 1, "lines": [{{"speaker": "{narrator}", "text": "…"}}]}}]}}"""
)

SHORT_RULE = """
[줄이기]
- 아래 「기본판」 그림책을 네 문장으로 줄인다. 단계마다 한 문장, 장소 · 문제 · 행동 · 결과 순서.
- 기본판에 없는 내용을 더하지 않는다. 기본판의 장면에서 그 단계를 가장 잘 보여 주는 것을 고른다.
"""


def _base_text(base: dict) -> str:
    """기본판 저장본(payload) → 쪽마다 한 줄 글."""

    return "\n".join(f"{p['page']}쪽 ({STAGE_KO[p['stage']]}): {p['text']}" for p in base["book"]["pages"])


def _user(combo: cs.Combo, base: dict) -> str:
    cards = USER_TEMPLATE.format(place=combo.place, problem=combo.problem, action=combo.action, result=combo.result)
    return f"아이가 고른 카드\n{cards}\n\n기본판 「{base['book']['title']}」\n{_base_text(base)}"


def _choices(combo: cs.Combo) -> dict[str, str]:
    return {"place": combo.place, "problem": combo.problem, "action": combo.action, "result": combo.result}


# ── 요청 만들기 / 결과 읽기 — 배치(scripts/batch_card_stories.py)와 바로 부르기가 같이 쓴다 ──
def request(kind: tuple[str, str], combo: cs.Combo, base: dict) -> tuple[str, str, bool]:
    """(system, user, json_mode). kind 는 (길이, 읽어주기)."""

    hero = cs.SLOT_NAME
    if kind == ("long", "characters"):
        system = CHARACTERS_SYSTEM.format(
            hero=hero, narrator=NARRATOR, protagonist=PROTAGONIST,
            types=", ".join(VOICE_TYPES), emotions=", ".join(EMOTION_TAGS),
        )
        return system, _user(combo, base), True
    if kind == ("short", "narration"):
        return SYSTEM_PROMPT + SHORT_RULE + KEEP, _user(combo, base), False
    if kind == ("short", "characters"):
        return script_prompt(hero) + SHORT_RULE + KEEP, _user(combo, base), True
    raise KeyError(kind)


def parse(kind: tuple[str, str], raw: str, combo: cs.Combo, base: dict, pages: int) -> tuple[dict, list[str], str]:
    """결과 → (저장본, 문제, 검열할 글). 형식이 틀리면 ValueError."""

    hero = cs.SLOT_NAME
    raw = cs.untemplatize(raw)  # 이름 자리 표시를 따라 쓴 답도 읽는다
    if kind == ("long", "characters"):
        bb = base["book"]
        try:
            got = json.loads(raw)
            lines = {int(p["page"]): p["lines"] for p in got["pages"]}
            cast = {**base.get("cast", {}), **{c["name"]: c.get("type", "") for c in got.get("characters") or []}}
            data = {
                "title": got.get("title") or bb["title"],
                "protagonist": hero,
                "characters": [{"name": k, "type": v} for k, v in cast.items()],
                "pages": [
                    {"page": p["page"], "stage": p["stage"], "action": p["action"],
                     "child_words": p.get("child_words") or "", "lines": lines[p["page"]]}
                    for p in bb["pages"]
                ],
                "order_pages": bb["order_pages"],
                "scene": {
                    "character_sheet": bb.get("character_sheet") or {},
                    "pages": [
                        {"page": p["page"], **(p.get("scene") or {"hero_on_page": True, "characters": [], "background": ""})}
                        for p in bb["pages"]
                    ],
                },
            }
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise ValueError(f"캐릭터 판 형식: {e}") from e
        book = parse_book(json.dumps(data, ensure_ascii=False), hero)
        problems = book_problems(book, _choices(combo), pages, True)
        if hero not in book.text():
            problems.append("주인공 이름이 없다")
        return cs.book_payload(book), problems, book.text()
    if kind == ("short", "narration"):
        text = " ".join(raw.split())
        if not text:
            raise ValueError("빈 글")
        return cs.short_payload(text=text), _short_checks(text, combo), text
    if kind == ("short", "characters"):
        script = parse_script(raw, hero)
        text = script_text(script)
        problems = _short_checks(text, combo) + ([] if has_dialogue(script) else ["대사가 없다"])
        payload = cs.short_payload(
            sentences=[[(ln.speaker, ln.text, ln.emotion) for ln in parts] for parts in script.sentences], cast=script.cast
        )
        return payload, problems, text
    raise KeyError(kind)


def make(writer, kind: tuple[str, str], combo: cs.Combo, base: dict, pages: int) -> tuple[dict, list[str]]:
    """바로 부르기 — 세 번까지 다시 쓴다."""

    system, user, json_mode = request(kind, combo, base)
    best = None
    for _ in range(TRIES):
        raw = writer.claude_raw(system, user, json_mode=json_mode)
        try:
            payload, problems, text = parse(kind, raw, combo, base, pages)
        except ValueError as e:
            log.info("%s 형식이 틀려 다시 씁니다: %s", kind, e)
            continue
        writer.check_input(text)
        best = (payload, problems)
        if not problems:
            break
    if best is None:
        raise ValueError(f"{kind} 를 받지 못했다")
    return best


def _short_checks(text: str, combo: cs.Combo) -> list[str]:
    out = []
    n = count_sentences(text)
    if n != SENTENCE_COUNT:
        out.append(f"{n}문장")
    for s, card in _choices(combo).items():
        if not mentions(card, text):
            out.append(f"{STAGE_KO[s]} 「{card}」 가 없다")
    return out


KINDS = (("long", "characters"), ("short", "narration"), ("short", "characters"))
VARIANTS = {k: (lambda k: (lambda w, c, b, pages: make(w, k, c, b, pages)))(k) for k in KINDS}
assert set(STAGES) == {"place", "problem", "action", "result"}
