"""OpenAI로 이야기를 만들고 부적절한 내용을 걸러낸다. 긴 이야기 대본은 Claude(Anthropic)가 먼저다.

글을 막 배우는 아이가 읽고 들을 글이라 문장을 짧고 쉽게 만드는 것이 가장 중요하다.
API 키가 없거나 호출이 실패하면 템플릿 문장으로 대체해 서비스가 멈추지 않게 한다.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import NamedTuple

import anthropic
from openai import OpenAI, OpenAIError

from app.core.config import get_settings
from app.services.book import Book, book_problems, book_prompt, book_user_message, parse_book, short_form_rule
from app.services.voices import EMOTION_TAGS, NARRATOR, PROTAGONIST, VOICE_TYPES

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """너는 글을 막 배우기 시작한 아이를 위한 이야기를 쓰는 작가야.

아이가 고른 네 단계(장소, 문제, 행동, 결과)로 짧은 이야기를 만들어.

규칙:
- 정확히 네 문장으로 쓴다. 각 단계마다 한 문장씩.
- 한 문장은 15자에서 25자 사이로 짧게 쓴다.
- 초등학교 1학년이 아는 쉬운 낱말만 쓴다.
- 어려운 말, 비유, 속담은 쓰지 않는다.
- 주인공은 "나"로 쓰고, 말투는 "~어요"로 끝낸다.
- 아이가 고른 네 단계의 내용을 반드시 모두 담는다.
- 아이가 고르지 않은 인물을 데려오지 않는다. 등장인물은 주인공과, 고른 카드에 꼭 필요한 인물만 쓴다
  (「친구가 울고 있었어요」면 친구, 「어른을 불렀어요」면 어른). 엄마 · 친구와 함께 갔다고 쓰지 않는다.
- 카드의 뜻을 바꾸거나 뒤집지 않는다. 「도와줬어요」는 주인공이 누군가를 도운 것이고,
  「숨었어요」는 주인공이 숨은 것이다. 주어는 주인공이다.
- 아이 생각이 앞뒤가 안 맞아도 고치지 않는다.
  우주에서 비가 와도, 숲에 공룡이 나와도 그대로 살려서 신기한 일로 이어 준다.
- 무섭거나 슬픈 결말로 끝내지 않는다.
- 이야기 본문만 쓰고 제목이나 설명은 붙이지 않는다."""

# 캐릭터 목소리 판 (S-13 「읽어주기」). 대사를 등장인물 목소리로 읽도록 화자를 붙인 대본을 받는다.
# 대사도 문장 안에 넣어 네 문장을 지킨다 — S-10 순서 맞추기 카드가 문장 단위다.
# 인물 종류로 목소리를, 감정으로 v4 태그를 고른다 (services/voices.py)
#
# 나레이션은 3인칭이다. 1인칭 「나는」을 여자 나레이터가 읽고 「나」의 대사를 남자아이 목소리가
# 읽으면 같은 「나」가 두 목소리가 되어 어색하다 (사용자, 2026-10-08).
# 주인공 이름은 아이가 S-05 다음에 짓는다. 나레이션만 판은 지금처럼 1인칭 (SYSTEM_PROMPT) —
# 아이가 자기 이야기로 받아들이게 하는 사회적 이야기 방식
_FIRST_PERSON = '- 주인공은 "나"로 쓰고, 말투는 "~어요"로 끝낸다.'
assert _FIRST_PERSON in SYSTEM_PROMPT


def josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 맞는 조사를 붙인다. josa("하늘", "은", "는") → 「하늘은」.

    한글이 아니면 받침 없는 쪽을 붙인다.
    """

    last = word[-1:] or " "
    has_final = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0
    return word + (with_final if has_final else without_final)


def script_prompt(name: str | None) -> str:
    """캐릭터 목소리 판의 지시문. name 이 없으면 GPT 가 짓고 protagonist 에 적는다."""

    if name:
        who = (
            f'- 주인공 이름은 "{name}"이다. 나레이션은 이야기 밖에서 들려주는 사람이라 '
            f'주인공을 "{name}"(이)라고 부른다. "나"라고 쓰지 않는다. 말투는 "~어요"로 끝낸다.'
        )
    else:
        who = (
            "- 주인공에게 쉬운 두 글자 이름을 지어 protagonist 에 적는다. "
            "나레이션은 이야기 밖에서 들려주는 사람이라 주인공을 그 이름으로 부른다. "
            '"나"라고 쓰지 않는다. 말투는 "~어요"로 끝낸다.'
        )
    hero = name or "토토"
    # GPT 가 예시의 조사를 그대로 따라 쓴다 (「하늘는」이 나온 적 있음). 예시부터 받침에 맞춘다
    hero_topic = josa(hero, "은", "는")
    return (
        SYSTEM_PROMPT.replace(_FIRST_PERSON, who)
        + f"""
- 네 문장 중 한두 문장에는 등장인물의 짧은 대사(5~12자)를 넣는다.
- 대사가 들어간 문장도 나레이션으로 끝낸다. 예: {hero_topic} 친구에게 "미안해." 하고 말했어요.
- 이름 뒤 조사는 받침에 맞춘다 (하늘은 · 하늘이 · 하늘과, 토토는 · 토토가 · 토토와).
- 대사는 그 인물이 직접 하는 말이다. 상황을 설명하지 않는다.
  좋은 예: 개구리가 "집에 가고 싶어." 하고 울었어요. 나쁜 예: 개구리가 "울고 있어요." 라고 말했어요.
- 말하는 인물은 주인공 말고 두 명까지. 다른 인물은 "친구", "엄마"처럼 부른다.

JSON 으로만 답한다. sentences 는 정확히 네 개이고, 하나에 마침표로 끝나는 문장 하나만 쓴다.
문장마다 말하는 사람과 말을 차례로 적는다.
나레이션의 speaker 는 "{NARRATOR}", 주인공 대사의 speaker 는 "{PROTAGONIST}" 이다.
대사의 text 에는 따옴표를 붙이지 않는다.
characters 에는 주인공 말고 대사가 있는 인물을 빠짐없이 적는다.
type 은 다음 중 하나다: {", ".join(VOICE_TYPES)}
대사에는 emotion 을 하나 붙인다: {", ".join(EMOTION_TAGS)}
{{"protagonist": "{hero}",
 "characters": [{{"name": "친구", "type": "아이-남"}}],
 "sentences": [
  [{{"speaker": "{NARRATOR}", "text": "{hero_topic} 학교에 갔어요."}}],
  [{{"speaker": "{NARRATOR}", "text": "{hero_topic} 친구에게"}},
   {{"speaker": "{PROTAGONIST}", "text": "미안해.", "emotion": "다정"}},
   {{"speaker": "{NARRATOR}", "text": "하고 말했어요."}}]
]}}"""
    )


class Line(NamedTuple):
    speaker: str
    text: str
    # 대사의 감정 (voices.EMOTION_TAGS 의 이름). 나레이션은 비어 있다
    emotion: str = ""


@dataclass
class Script:
    sentences: list[list[Line]]
    # 인물 이름 → 종류 (voices.VOICE_TYPES 의 이름). 주인공은 넣지 않는다
    cast: dict[str, str] = field(default_factory=dict)
    # 주인공 이름. 아이가 짓지 않았으면 GPT 가 지은 이름
    protagonist: str = ""


# 프론트 `sentences()` (src/lib.ts) 와 같은 기준. 따옴표 안의 마침표에서는 끊지 않는다
_SENTENCE = re.compile(r"(?:“[^”]*”?|[^.!?。“])+[.!?。]*")
SENTENCE_COUNT = 4

USER_TEMPLATE = """장소: {place}
문제: {problem}
행동: {action}
결과: {result}"""

# 이 값들이 그대로 이야기에 들어가므로, 부적절한 입력은 사전에 걸러야 한다.
MODERATION_MODEL = "omni-moderation-latest"
STORY_MODEL = "gpt-4o-mini"


class StoryBlocked(Exception):
    """부적절한 내용이 감지되어 이야기를 만들지 않았다."""


def fallback_text(place: str, problem: str, action: str, result: str) -> str:
    """OpenAI를 쓸 수 없을 때 사용하는 템플릿 문장."""

    return f"오늘 나는 {place}에 갔어요. 그런데 {problem}. 그래서 나는 {action}. 그랬더니 {result}."


def parse_script(raw: str, name: str | None = None) -> Script:
    """GPT 가 준 JSON 대본을 문장별 Line 목록과 인물 목록으로 바꾼다. 형식이 틀리면 ValueError.

    GPT 가 주인공 대사의 speaker 를 이름으로 적어도 주인공으로 본다.
    """

    try:
        data = json.loads(raw)
        sentences = [
            [
                Line(
                    str(p.get("speaker") or NARRATOR).strip() or NARRATOR,
                    str(p["text"]).strip(),
                    str(p.get("emotion") or "").strip(),
                )
                for p in parts
                if str(p.get("text") or "").strip()
            ]
            for parts in data["sentences"]
        ]
        cast = {
            str(c["name"]).strip(): str(c.get("type") or "").strip()
            for c in data.get("characters") or []
            if str(c.get("name") or "").strip()
        }
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
        raise ValueError(f"대본 형식이 아님: {e}") from e
    protagonist = name or str(data.get("protagonist") or "").strip()
    if protagonist:
        sentences = [
            [ln._replace(speaker=PROTAGONIST) if ln.speaker == protagonist else ln for ln in s]
            for s in sentences
        ]
        cast.pop(protagonist, None)
    cast.pop(PROTAGONIST, None)
    sentences = [s for s in sentences if s]
    if not sentences:
        raise ValueError("빈 대본")
    return Script(sentences, cast, protagonist)


def script_text(script: Script) -> str:
    """화면에 보일 이야기 글. 대사는 “ ” 로 감싼다. 감정은 글에 넣지 않는다."""

    return " ".join(
        " ".join(line.text if line.speaker == NARRATOR else f"“{line.text}”" for line in parts)
        for parts in script.sentences
    )


def count_sentences(text: str) -> int:
    return sum(1 for m in _SENTENCE.finditer(text) if m.group().strip())


def has_dialogue(script: Script) -> bool:
    return any(line.speaker != NARRATOR for parts in script.sentences for line in parts)


def json_part(text: str) -> str:
    """모델 답에서 JSON 부분만. Claude 는 JSON 모드가 없어 앞뒤에 말이나 ``` 를 붙일 때가 있다."""

    a = text.find("{")
    if a == -1:
        return text
    # 첫 JSON 하나만. 뒤에 설명이나 두 번째 JSON 이 붙어 「Extra data」로 깨진 적이 있다 (2026-10-28 배치)
    try:
        _, end = json.JSONDecoder().raw_decode(text[a:])
        return text[a : a + end]
    except json.JSONDecodeError:
        b = text.rfind("}")
        return text[a : b + 1] if b > a else text


def cache_blocks(system: str, reference: str) -> list[dict]:
    """대본 지시문을 Anthropic 캐시 블록으로 (2026-11-09 「이야기 생성 시간 단축」). 글은 그대로 두고 나눠 보내기만 한다.

    - 이야기마다 같은 앞부분(reference 와 같은 데까지: 나레이션 판 약 4,300자, 캐릭터 판은 이름 앞 약 2,200자)
    - 지시문 전체 (같은 이야기 안에서 고치거나 다시 쓸 때)
    캐시가 되는 최소 길이보다 짧으면 Anthropic 이 그냥 캐시하지 않는다(오류 아님)"""

    n = 0
    while n < min(len(system), len(reference)) and system[n] == reference[n]:
        n += 1
    blocks = []
    if 0 < n < len(system):
        blocks.append({"type": "text", "text": system[:n], "cache_control": {"type": "ephemeral"}})
    blocks.append({"type": "text", "text": system[n:] if blocks else system, "cache_control": {"type": "ephemeral"}})
    return blocks


def system_text(system: str | list[dict]) -> str:
    return system if isinstance(system, str) else "".join(b["text"] for b in system)


class PageStream:
    """흘려 받는 대본 JSON 에서 맨 위 "pages": [ … ] 의 쪽 객체가 닫힐 때마다 on_page(dict) 를 부른다
    (2026-11-09: 대본이 다 끝나기 전에 그 쪽 음성을 먼저 만들려고). 문자열 안 괄호 · 따옴표 이스케이프는 건너뛴다"""

    def __init__(self, on_page: Callable[[dict], None]) -> None:
        self.on_page, self.buf, self.pos = on_page, "", 0
        self.state = "seek"          # seek → array → done
        self.depth, self.start, self.in_str, self.esc = 0, -1, False, False

    def feed(self, chunk: str) -> None:
        self.buf += chunk
        if self.state == "seek":
            i = self.buf.find('"pages"')
            if i < 0:
                return
            j = self.buf.find("[", i)
            if j < 0:
                return
            self.state, self.pos = "array", j + 1
        while self.state == "array" and self.pos < len(self.buf):
            c = self.buf[self.pos]
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif c == "\\":
                    self.esc = True
                elif c == '"':
                    self.in_str = False
            elif c == '"':
                self.in_str = True
            elif c in "{[":
                if self.depth == 0 and c == "{":
                    self.start = self.pos
                self.depth += 1
            elif c in "}]":
                if self.depth == 0 and c == "]":
                    self.state = "done"
                else:
                    self.depth -= 1
                    if self.depth == 0 and c == "}" and self.start >= 0:
                        try:
                            self.on_page(json.loads(self.buf[self.start:self.pos + 1]))
                        except Exception:  # noqa: BLE001 미리 만들기는 덤이라 쪽 하나가 깨져도 대본은 그대로
                            log.debug("흘려 받은 쪽을 읽지 못함", exc_info=True)
                        self.start = -1
            self.pos += 1


REPAIR = """방금 쓴 대본에서 아래 문제만 고쳐라.
{problems}
- 고친 쪽만, 처음과 같은 쪽 객체 형식으로 "pages" 에 넣는다. 쪽마다 "page" 번호는 그대로다. 고치지 않은 쪽은 넣지 않는다.
- 장면 계획(scene)을 고쳐야 하면 "scene" 을 처음 형식 그대로 통째로 넣는다. 아니면 넣지 않는다.
- 쪽 수 · 단계(stage) · 순서는 바꾸지 않는다. 다른 쪽 내용과 이어지게 쓴다.
JSON 으로만 답한다. {{"pages": [ … ], "scene": {{ … }}}}"""


def merge_repair(raw: str, fix: str) -> str:
    """고친 쪽(과 장면 계획)을 원래 대본 JSON 에 끼운다. 형식이 틀리면 ValueError"""

    try:
        data, got = json.loads(raw), json.loads(fix)
        for page in got.get("pages") or []:
            k = int(page["page"])
            if not 1 <= k <= len(data["pages"]):
                raise ValueError(f"없는 쪽 {k}")
            data["pages"][k - 1] = page
        if got.get("scene"):
            data["scene"] = got["scene"]
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
        raise ValueError(f"고친 쪽 형식이 아님: {e}") from e
    return json.dumps(data, ensure_ascii=False)


class StoryWriter:
    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        claude_key: str = "",
        claude_timeout: float = 180.0,
    ) -> None:
        self.client = OpenAI(api_key=api_key, timeout=timeout)
        # 긴 이야기 대본용. 없으면 OpenAI 모델로 쓴다
        self.claude = (
            anthropic.Anthropic(api_key=claude_key, timeout=claude_timeout) if claude_key else None
        )
        # 바로 앞 호출에서 실제로 답한 모델. 미리 만든 이야기에 남겨, 예비 모델로 넘어간 편을 골라낸다
        self.last_model = ""
        # Claude 토큰 합계 (미리 만들기에서 비용을 재려고). 다시 쓴 시도도 들어간다
        self.usage = {"input": 0, "output": 0}

    def _count(self, res) -> None:
        u = getattr(res, "usage", None)
        if u is not None:
            self.usage["input"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output"] += getattr(u, "output_tokens", 0) or 0

    def claude_raw(self, system: str, user: str, json_mode: bool = False, effort: str = "low") -> str:
        """Claude 한 번 — 예비 모델 없이. 미리 만들기의 고쳐 쓰기(card_variants)가 쓴다. 실패하면 예외."""

        if self.claude is None:
            raise RuntimeError("ANTHROPIC_API_KEY 가 없다")
        s = get_settings()
        res = self.claude.messages.create(
            model=s.story_short_claude_model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
        )
        self._count(res)
        self.last_model = s.story_short_claude_model
        text = "".join(b.text for b in res.content if b.type == "text").strip()
        return json_part(text) if json_mode else text

    def short_completion(self, system: str, user: str, json_mode: bool, openai_tokens: int) -> str:
        """짧은 이야기 한 번. Claude 가 있으면 Claude, 실패하면 그 시도만 gpt-4o-mini."""

        s = get_settings()
        if self.claude is not None and s.story_short_provider == "anthropic":
            try:
                res = self.claude.messages.create(
                    model=s.story_short_claude_model,
                    max_tokens=4000,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": s.story_short_effort},
                )
                self._count(res)
                text = "".join(b.text for b in res.content if b.type == "text").strip()
                if text:
                    self.last_model = s.story_short_claude_model
                    return json_part(text) if json_mode else text
                log.info("Claude 짧은 이야기가 비어 gpt-4o-mini 로 씁니다")
            except anthropic.APIError:
                log.exception("Claude 짧은 이야기 호출 실패, 이번 시도는 gpt-4o-mini 로 씁니다")
        res = self.client.chat.completions.create(
            model=STORY_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.8,
            max_tokens=openai_tokens,
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
        )
        self.last_model = STORY_MODEL
        return (res.choices[0].message.content or "").strip()

    def check_input(self, text: str) -> None:
        """아이가 입력한 내용에 부적절한 표현이 있는지 확인한다. (무료)"""

        res = self.client.moderations.create(model=MODERATION_MODEL, input=text)
        if res.results[0].flagged:
            categories = [
                name for name, flagged in res.results[0].categories.model_dump().items() if flagged
            ]
            log.warning("입력이 moderation에 걸림: %s", categories)
            raise StoryBlocked(", ".join(categories))

    def write(self, place: str, problem: str, action: str, result: str, hero_form: dict | None = None) -> str:
        user_input = USER_TEMPLATE.format(
            place=place, problem=problem, action=action, result=result
        )
        self.check_input(user_input)

        system = SYSTEM_PROMPT + short_form_rule(hero_form)   # 사람이 아닌 주인공 (4족형 …)
        text = self.short_completion(system, user_input, json_mode=False, openai_tokens=300)
        # Claude 는 문장마다 줄을 바꿔 올 때가 있다. 화면 · 음성은 한 줄 글을 받는다
        text = " ".join(text.split())

        if not text:
            raise OpenAIError("빈 응답")

        # 생성된 이야기도 한 번 더 확인한다
        self.check_input(text)
        return text

    def write_script(
        self, place: str, problem: str, action: str, result: str, name: str | None = None,
        hero_form: dict | None = None,
    ) -> Script:
        """캐릭터 목소리 판. 화자 · 감정을 붙인 네 문장 대본과 인물 목록을 만든다.

        나레이션은 주인공을 name 으로 부르는 3인칭이다 (없으면 GPT 가 짓는다).
        """

        user_input = USER_TEMPLATE.format(
            place=place, problem=problem, action=action, result=result
        )
        self.check_input(user_input)
        if name:
            self.check_input(name)

        # 네 문장을 넘기거나(다섯 문장) 대사를 빼먹을 때가 있다. S-10 카드가 문장 단위라
        # 맞을 때까지 세 번까지 쓴다. 끝내 안 맞으면 마지막 것을 쓴다
        script = None
        for _ in range(3):
            raw = self.short_completion(script_prompt(name) + short_form_rule(hero_form), user_input,
                                        json_mode=True, openai_tokens=600)
            try:
                script = parse_script(raw, name)
            except ValueError as e:
                log.info("대본 형식이 틀려 다시 씁니다: %s", e)
                continue
            # 대사가 없으면 캐릭터 목소리 판의 의미가 없다 (대사를 안 넣은 적 있음)
            if count_sentences(script_text(script)) == SENTENCE_COUNT and has_dialogue(script):
                break
            log.info(
                "대본이 %d문장, 대사 %s 라 다시 씁니다",
                count_sentences(script_text(script)),
                "있음" if has_dialogue(script) else "없음",
            )

        if script is None:
            raise ValueError("형식이 맞는 대본을 받지 못했다")  # 부르는 쪽이 나레이션 판으로 대신한다
        self.check_input(script_text(script))
        return script

    def write_book(
        self,
        choices: dict[str, str],
        spoken: set[str],
        pages: int,
        characters: bool,
        name: str | None = None,
        extras: dict[str, str] | None = None,
        opening: str | None = None,
        hero_form: dict | None = None,
        on_page: Callable[[dict], None] | None = None,
    ) -> Book:
        """긴 이야기. 네 칸(과 아이가 말로 덧붙인 말)을 뼈대로 쪽 단위 대본을 만든다.

        on_page: 첫 시도를 흘려 받으며 쪽 객체가 닫힐 때마다 부른다 (부르는 쪽이 그 쪽 음성을 먼저 만든다).
        검사에 걸리면 먼저 틀린 쪽만 고쳐 받고(REPAIR, 출력이 짧아 빠르다), 그래도 안 되면 처음부터 다시 쓴다 (2026-11-09).

        형식과 지시문은 services/book.py.

        아이가 고른 내용이 그 단계 쪽에 없거나 쪽 수가 크게 어긋나면 세 번까지 다시 쓴다.
        끝내 안 맞으면 마지막 것을 쓴다 — 형식만 맞으면 이야기는 나온다.
        """

        extras = {k: v for k, v in (extras or {}).items() if v}
        user_input = book_user_message(choices, spoken, extras, opening)
        self.check_input(user_input)
        if name:
            self.check_input(name)

        system = book_prompt(pages, characters, name, extras, place=choices.get("place"), hero_form=hero_form)
        # 이야기마다 같은 앞부분은 캐시한다: 이름 · 장소 · 덧붙인 말 · 주인공 모습만 다른 지시문과 겹치는 데까지
        blocks = cache_blocks(system, book_prompt(pages, characters, "§", None, place=None, hero_form=None))
        # 아이 입력이 걸리면 위에서 막는다(StoryBlocked). AI 가 쓴 글이 걸리면 아이 탓이 아니므로
        # 다시 쓰게 하고, 끝내 걸리면 OpenAIError — 부르는 쪽이 틀 문장 그림책으로 대신한다
        # (「넘어졌어요」 이야기의 대본이 violence 로 걸린 적 있음, 2026-10-12)
        best = None
        for attempt in range(3):
            raw = self.book_completion(blocks, user_input, on_page if attempt == 0 else None)
            book = self._accept(raw, name, spoken)
            if book is None:
                continue
            best = book
            problems = book_problems(book, choices, pages, characters)
            if not problems:
                break
            # 쪽 배치가 틀린 것은 고치기로 안 된다 (쪽 수 · 단계를 바꾸지 못하게 했다), 처음부터 다시
            if not any(x.startswith("쪽 배치") for x in problems):
                try:
                    fixed = merge_repair(raw, self.book_fix(blocks, user_input, raw, problems))
                except (ValueError, anthropic.APIError, OpenAIError) as e:
                    log.info("틀린 쪽만 고치기 실패, 처음부터 다시 씁니다: %s", e)
                else:
                    repaired = self._accept(fixed, name, spoken)
                    if repaired is not None:
                        left = book_problems(repaired, choices, pages, characters)
                        best = repaired if len(left) <= len(problems) else best
                        if not left:
                            log.info("그림책 대본의 틀린 쪽만 고쳤습니다: %s", "; ".join(problems))
                            break
                        problems = left
            log.info("그림책 대본을 다시 씁니다: %s", "; ".join(problems))
        if best is None:
            raise OpenAIError("쓸 수 있는 그림책 대본을 받지 못했다")
        return best

    def _accept(self, raw: str, name: str | None, spoken: set[str]) -> Book | None:
        """대본 JSON 을 Book 으로 읽고 검열까지. 형식이 틀리거나 AI 글이 검열에 걸리면 None (다시 쓴다)"""

        try:
            book = parse_book(raw, name)
        except ValueError as e:
            log.info("그림책 대본 형식이 틀려 다시 씁니다: %s", e)
            return None
        # 「내가 말한 부분」은 아이가 말로 덧붙인 단계에만. GPT 가 카드 글자를 적어 와도 지운다
        for p in book.pages:
            if p.stage not in spoken:
                p.child_words = ""
        try:
            self.check_input(book.text())
        except StoryBlocked:
            log.info("AI 가 쓴 그림책 대본이 moderation 에 걸려 다시 씁니다")
            return None
        return book

    def book_fix(self, system: str | list[dict], user: str, raw: str, problems: list[str]) -> str:
        """방금 쓴 대본의 틀린 쪽(과 장면 계획)만 고쳐 받는다. 고친 쪽만 돌아와 출력이 짧다 (생각은 적게)"""

        ask = REPAIR.format(problems="\n".join(f"- {x}" for x in problems))
        msgs = [{"role": "user", "content": user}, {"role": "assistant", "content": raw}, {"role": "user", "content": ask}]
        s = get_settings()
        if self.claude is not None and s.story_book_provider == "anthropic":
            res = self.claude.messages.create(
                model=s.story_book_claude_model, max_tokens=8000, system=system, messages=msgs,
                output_config={"effort": "low"},
            )
            self._count(res)
            return json_part("".join(b.text for b in res.content if b.type == "text"))
        res = self.client.chat.completions.create(
            model=s.story_book_model,
            messages=[{"role": "system", "content": system_text(system)}, *msgs],
            response_format={"type": "json_object"},
            **({"max_completion_tokens": 8000} if s.story_book_model.startswith(("gpt-5", "o3", "o4")) else {"max_tokens": 2000}),
        )
        return res.choices[0].message.content or ""

    def book_completion(self, system: str | list[dict], user: str, on_page: Callable[[dict], None] | None = None) -> str:
        """그림책 대본 JSON 한 번. Claude 가 있으면 Claude, 실패하면 그 시도만 OpenAI 모델로.

        on_page 가 있으면 흘려 받으며 쪽 객체가 닫힐 때마다 부른다 (PageStream)."""

        s = get_settings()
        if self.claude is not None and s.story_book_provider == "anthropic":
            try:
                kw = dict(
                    model=s.story_book_claude_model,
                    # 생각 토큰도 여기에 든다. 16000 을 넘기면 스트리밍으로 받아야 한다
                    max_tokens=16000,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": s.story_book_effort},
                )
                if on_page is not None and hasattr(self.claude.messages, "stream"):
                    pager = PageStream(on_page)
                    with self.claude.messages.stream(**kw) as st:
                        for chunk in st.text_stream:
                            pager.feed(chunk)
                        res = st.get_final_message()
                else:
                    res = self.claude.messages.create(**kw)
                self._count(res)
                text = "".join(b.text for b in res.content if b.type == "text")
                if res.stop_reason == "max_tokens":
                    log.info("Claude 대본이 토큰 한도에서 잘렸습니다")
                self.last_model = s.story_book_claude_model
                return json_part(text)
            except anthropic.APIError:
                log.exception("Claude 대본 호출 실패, 이번 시도는 OpenAI 모델로 씁니다")
        model = s.story_book_model
        params: dict = {"max_tokens": 2000, "temperature": 0.8}
        if model.startswith(("gpt-5", "o3", "o4")):
            # 추론 모델은 temperature 를 받지 않고 토큰 한도 이름이 다르다
            params = {"max_completion_tokens": 8000}
        res = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_text(system)}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            **params,
        )
        self.last_model = model
        return res.choices[0].message.content or ""


def get_story_writer() -> StoryWriter | None:
    """API 키가 없으면 None을 돌려준다. 호출하는 쪽에서 템플릿으로 대체한다."""

    s = get_settings()
    if not s.openai_api_key:
        return None
    return StoryWriter(
        s.openai_api_key,
        timeout=s.openai_timeout_seconds,
        claude_key=s.anthropic_api_key,
        claude_timeout=s.anthropic_timeout_seconds,
    )
