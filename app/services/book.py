"""긴 이야기 — 아이가 고른 네 칸으로 쪽 단위 그림책 대본을 만든다.

지금까지는 네 칸 → 네 문장이었다. 긴 이야기는 같은 네 칸(과 아이가 말로 덧붙인 말)을 뼈대로
GPT 가 여러 쪽으로 풀어 쓴다. **이야기의 주인은 아이다** —
쪽마다 어느 칸에서 나왔는지(stage)를 적고, 아이가 고르지 않은 큰 사건을 만들지 않게 한다.
대본 초안 · 검사기는 hankan-story/experiments/storybook-script.

OpenAI 를 쓸 수 없으면 틀 문장으로 쪽을 채운다 (fallback_book). 그래서 키 없이도 흐름이 끝까지 돈다.
"""

import json
import re
from dataclasses import dataclass, field

from app.services.voices import EMOTION_TAGS, NARRATOR, PROTAGONIST, VOICE_TYPES

STAGES = ("place", "problem", "action", "result")
STAGE_KO = {"place": "장소", "problem": "문제", "action": "행동", "result": "결과"}
# 쪽마다 캐릭터가 하는 동작. 렌더 동작과의 연결은 다음 단계 (지금은 이야기 전체에 영상 하나)
ACTIONS = ("idle", "walk", "run", "jump", "wave", "dance")
# 단계마다 쪽 수의 (최소, 최대). 합이 pages 가 되게 split_pages 가 나눈다
STAGE_PAGES = {"place": (1, 2), "problem": (2, 3), "action": (2, 3), "result": (2, 3)}
MIN_PAGES, MAX_PAGES = 7, 11


def split_pages(pages: int) -> dict[str, int]:
    """pages 쪽을 단계별로 나눈다.

    최소에서 시작해 결과 → 문제 → 행동 → 장소 순으로 하나씩 늘린다.
    """

    pages = max(MIN_PAGES, min(MAX_PAGES, pages))
    n = {s: lo for s, (lo, _) in STAGE_PAGES.items()}
    order = ("result", "problem", "action", "place")
    i = 0
    while sum(n.values()) < pages and i < 40:
        s = order[i % 4]
        if n[s] < STAGE_PAGES[s][1]:
            n[s] += 1
        i += 1
    return n


@dataclass
class Page:
    page: int
    stage: str
    action: str
    # (speaker, text, emotion). story_writer.Line 과 같은 모양 — 순환 import 를 피하려고 튜플로 둔다
    lines: list[tuple[str, str, str]]
    # 이 쪽에 그대로 들어간 아이 말. 화면에서 「내가 말한 부분」으로 보인다
    child_words: str = ""

    def text(self) -> str:
        return " ".join(t if s == NARRATOR else f"“{t}”" for s, t, _ in self.lines)


@dataclass
class Book:
    title: str
    pages: list[Page]
    # S-10 순서 맞추기에 쓸 쪽 번호. 단계마다 하나, 장소 · 문제 · 행동 · 결과 순
    order_pages: list[int]
    protagonist: str = ""
    # 인물 이름 → 종류 (voices.VOICE_TYPES). 주인공은 넣지 않는다
    cast: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        return " ".join(p.text() for p in self.pages)

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "pages": [
                {
                    "page": p.page,
                    "stage": p.stage,
                    "action": p.action,
                    "text": p.text(),
                    "child_words": p.child_words,
                    "lines": [{"speaker": s, "text": t, "emotion": e} for s, t, e in p.lines],
                }
                for p in self.pages
            ],
            "order_pages": self.order_pages,
        }


SYSTEM = """너는 아이가 만든 이야기를 그림책 대본으로 옮겨 적는 「이야기 정리 도우미」야.
이야기의 주인은 아이다. 너는 아이가 고르고 말한 것을 쪽으로 나누고 읽기 좋게 이어 줄 뿐이다.

[아이 몫 지키기 — 가장 중요]
1. 이야기는 **꼭 {pages}쪽**이다. 쪽마다 stage 에 어느 단계에서 나온 쪽인지 적는다.
   쪽 배치는 정해져 있다: {page_plan}
   stage 는 place · problem · action · result 중 하나다.
2. 단계 첫 쪽에는 아이가 고른 카드 글자를 거의 그대로 쓴다
   (카드 「도와줬어요」 → 「… 도와줬어요.」).
   **카드의 뜻을 바꾸거나 뒤집지 않는다.**
   「도와줬어요」는 주인공이 누군가를 도운 것이지 도움을 받은 것이 아니다.
   「숨었어요」는 주인공이 숨은 것이다. 주어는 주인공이다.
3. 「아이가 한 말」은 뜻을 바꾸지 않고 거의 그대로 문장에 넣는다.
   그 쪽의 child_words 에 아이 말을 그대로 적는다.
   아이 말과 카드 글자는 **이야기 문장으로 녹여 쓴다.** 따옴표 대사로 옮기지 않는다.
   (아이 말 「친구한테 도와달라고 했어」 → 「나는 친구에게 도와 달라고 했어요.」,
   카드 「넘어졌어요」 → 「나는 그만 넘어졌어요.」 — 「“넘어졌어요.”」처럼 쓰지 않는다)
4. 아이가 고르지 않은 큰 일을 새로 만들지 않는다.
   다른 장소로 옮겨 가기, 새 문제, 새 악당, 다른 결말.
   등장인물은 주인공과, 고른 일에 꼭 필요한 인물만 쓴다 (「도와줬어요」면 도움을 받는 누군가).
   고른 내용에 없는 친구 · 가족을 처음부터 데려오지 않는다.
5. 네가 더해도 되는 것: 아이가 고른 일을 **장면으로 자세히 펼쳐 보이는 것**.
   무엇이 보이고 들렸는지, 주인공 마음이 어땠는지, 누가 무슨 말을 했는지,
   고른 일이 어떻게 일어났는지(예: 길을 잃었다면 어쩌다 길을 잃었는지). 앞 쪽과 이어 주는 말.
   펼쳐 보이는 것은 되지만, 아이가 고르지 않은 다른 사건으로 이야기를 바꾸면 안 된다.
6. 아이 생각이 앞뒤가 안 맞아도 고치지 않는다.
   우주에서 비가 와도, 숲에 공룡이 나와도 그대로 살려서 신기한 일로 이어 준다.

[동화책처럼 — 밋밋하게 쓰지 않는다]
- 첫 쪽은 장면을 그려 주며 시작한다 (예: 「햇살이 반짝이는 아침이었어요.」).
  「오늘 나는 …에 갔어요」 한 줄로 끝내지 않는다.
- 쪽마다 소리나 모양을 흉내 내는 말을 하나쯤 넣는다 (쿵쿵, 반짝반짝, 살금살금, 두근두근).
- 주인공의 마음을 보여 준다 (설렘, 걱정, 용기, 기쁨). 「~했어요」만 늘어놓지 않는다.
- 짧은 대사를 넣는다. 대사는 “ ” 로 감싼다.
- 같은 말을 되풀이해 리듬을 만든다 (「한 걸음, 또 한 걸음」).
- 문제 단계의 마지막 쪽이나 행동 단계 앞에는 다음 쪽이 궁금해지는 말을 둔다
  (「그런데 그때였어요!」).
- 마지막 쪽은 주인공의 마음이나 하루를 돌아보는 말로 따뜻하게 끝낸다.

[읽기 쉽게]
- 쪽마다 문장 2~4개. 문장 하나는 15~35자.
- 초등학교 1학년이 아는 쉬운 낱말. 비유 · 속담 · 어려운 한자말은 쓰지 않는다. 말투는 「~어요」.
{hero_rule}
- 무섭거나 슬픈 결말로 끝내지 않는다. 결과가 슬퍼도 마지막 쪽은 따뜻하게 마무리한다.

[안전]
- 몸이 다치는 모습을 자세히 그리지 않는다.
- 따라 하면 다칠 수 있는 행동은 넣지 않는다:
  높은 곳에서 뛰어내리기, 불 · 가스 · 전기, 찻길, 약 많이 먹기, 모르는 사람 따라가기.
  아이 말에 이런 것이 있으면 마법이나 상상 도구로 바꿔 쓴다.

[캐릭터 동작]
- 쪽마다 주인공이 그 쪽에서 하는 동작 action 을 하나 고른다:
  idle(가만히), walk(걷기), run(뛰기), jump(점프), wave(손 흔들기), dance(춤)

{dialogue_rule}[순서 맞추기]
- order_pages 에 네 단계를 하나씩 대표하는 쪽 번호 4개를 장소 · 문제 · 행동 · 결과 순서로 적는다.

JSON 으로만 답한다.
{{"title": "8~15자 제목",
 "protagonist": "{hero}",
 "characters": [{{"name": "친구", "type": "아이-남"}}],
 "pages": [
  {{"page": 1, "stage": "place", "action": "walk", "child_words": "",
    "lines": [{{"speaker": "{narrator}", "text": "…"}}]}}
 ],
 "order_pages": [1, 3, 5, 7]}}"""

HERO_FIRST = (
    '- 주인공은 「나」로 쓴다. lines 의 speaker 는 모두 "{narrator}" 이다.\n'
    "  대사는 나레이터가 읽으므로 나레이션 text 안에 “ ” 로 넣는다."
)
HERO_THIRD = (
    "- 주인공 이름은 「{hero}」이다. 나레이션은 이야기 밖에서 들려주는 사람이라 "
    "주인공을 「{hero}」(이)라고 부른다. 「나」라고 쓰지 않는다. 이름 뒤 조사는 받침에 맞춘다."
)
HERO_THIRD_UNNAMED = (
    "- 주인공에게 쉬운 두 글자 이름을 지어 protagonist 에 적는다. "
    "나레이션은 주인공을 그 이름으로 부른다 (3인칭). "
    "「나」라고 쓰지 않는다. 이름 뒤 조사는 받침에 맞춘다."
)
DIALOGUE = """[대사]
- 쪽의 절반 이하에만 등장인물의 짧은 대사(5~15자)를 넣는다. 대사는 그 인물이 직접 하는 말이다.
- 나레이션 speaker 는 "{narrator}", 주인공 대사 speaker 는 "{protagonist}".
  대사 text 에는 따옴표를 붙이지 않는다.
- 말하는 인물은 주인공 말고 두 명까지. characters 에 빠짐없이 적고 type 은 다음 중 하나: {types}
- 대사에는 emotion 을 하나 붙인다: {emotions}

"""


def page_plan(n: dict[str, int]) -> str:
    """「1쪽 place · 2~3쪽 problem · …」. GPT 가 쪽 수를 단계마다 지키게 쪽 번호를 박아 준다."""

    out, k = [], 1
    for s in STAGES:
        a, b = k, k + n[s] - 1
        out.append(f"{a}쪽 {s}" if a == b else f"{a}~{b}쪽 {s}")
        k = b + 1
    return " · ".join(out)


def book_prompt(pages: int, characters: bool, name: str | None) -> str:
    n = split_pages(pages)
    if characters:
        hero = name or "토토"
        rule = (HERO_THIRD if name else HERO_THIRD_UNNAMED).format(hero=hero)
        dialogue = DIALOGUE.format(
            narrator=NARRATOR,
            protagonist=PROTAGONIST,
            types=", ".join(VOICE_TYPES),
            emotions=", ".join(EMOTION_TAGS),
        )
    else:
        hero, rule, dialogue = "나", HERO_FIRST.format(narrator=NARRATOR), ""
    return SYSTEM.format(
        pages=sum(n.values()),
        page_plan=page_plan(n),
        hero_rule=rule,
        dialogue_rule=dialogue,
        hero=hero,
        narrator=NARRATOR,
    )


def book_user_message(choices: dict[str, str], spoken: set[str]) -> str:
    """단계마다 한 줄. 아이가 말로 덧붙인 단계는 「아이가 한 말」로 알린다 (child_words 를 적게)."""

    lines = []
    for s in STAGES:
        label = "아이가 한 말" if s in spoken else "아이가 고른 카드"
        lines.append(f'{STAGE_KO[s]} — {label}: "{choices[s]}"')
    return "\n".join(lines)


def parse_book(raw: str, name: str | None = None) -> Book:
    """GPT 가 준 JSON 을 Book 으로. 형식이 틀리면 ValueError.

    고칠 수 있는 것(동작 이름 · 순서 맞추기 쪽)은 고친다.
    """

    try:
        data = json.loads(raw)
        pages = []
        for i, p in enumerate(data["pages"], start=1):
            stage = str(p.get("stage") or "").strip()
            if stage not in STAGES:
                raise ValueError(f"{i}쪽 stage {stage!r}")
            lines = [
                (
                    str(ln.get("speaker") or NARRATOR).strip() or NARRATOR,
                    str(ln["text"]).strip(),
                    str(ln.get("emotion") or "").strip(),
                )
                for ln in p["lines"]
                if str(ln.get("text") or "").strip()
            ]
            if not lines:
                raise ValueError(f"{i}쪽이 비었다")
            action = str(p.get("action") or "idle").strip()
            pages.append(
                Page(
                    i,
                    stage,
                    action if action in ACTIONS else "idle",
                    lines,
                    str(p.get("child_words") or "").strip(),
                )
            )
        cast = {
            str(c["name"]).strip(): str(c.get("type") or "").strip()
            for c in data.get("characters") or []
            if str(c.get("name") or "").strip()
        }
        title = str(data.get("title") or "").strip()
        order = [int(x) for x in data.get("order_pages") or []]
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
        raise ValueError(f"대본 형식이 아님: {e}") from e

    stages = [p.stage for p in pages]
    if stages != sorted(stages, key=STAGES.index) or set(stages) != set(STAGES):
        raise ValueError(f"단계 순서가 맞지 않음: {stages}")

    protagonist = name or str(data.get("protagonist") or "").strip()
    if protagonist and protagonist != "나":
        for p in pages:
            p.lines = [(PROTAGONIST if s == protagonist else s, t, e) for s, t, e in p.lines]
        cast.pop(protagonist, None)
    cast.pop(PROTAGONIST, None)

    # 순서 맞추기 쪽: 단계마다 하나가 아니면 단계 첫 쪽으로
    first = {s: next(p.page for p in pages if p.stage == s) for s in STAGES}
    if len(order) != 4 or [
        next((p.stage for p in pages if p.page == k), None) for k in order
    ] != list(STAGES):
        order = [first[s] for s in STAGES]
    return Book(title, pages, order, "" if protagonist == "나" else protagonist, cast)


_SENT = re.compile(r"[^.!?。]+[.!?。]")


def book_problems(book: Book, choices: dict[str, str], pages: int, characters: bool) -> list[str]:
    """다시 쓰게 할 만큼 어긋난 것. 비어 있으면 받아들인다."""

    out = []
    want = split_pages(pages)
    got = {s: sum(p.stage == s for p in book.pages) for s in STAGES}
    if got != want:
        out.append(f"쪽 배치 {got} (요청 {want})")
    speakers = {s for p in book.pages for s, _, _ in p.lines}
    if not characters and speakers - {NARRATOR}:
        out.append("나레이션만 판인데 대사 화자가 있다")
    if characters and len(speakers - {NARRATOR, PROTAGONIST}) > 2:
        out.append("말하는 인물이 너무 많다")
    for s in STAGES:
        text = " ".join(p.text() for p in book.pages if p.stage == s)
        if not mentions(choices[s], text):
            out.append(f"{STAGE_KO[s]} 「{choices[s]}」 가 그 단계 쪽에 없다")
    return out


def mentions(card: str, text: str) -> bool:
    """카드(또는 아이 말)가 글에 들어 있나.

    활용이 바뀌어도(막았어 → 막고) 알아보게 낱말 앞 두 글자로 본다.

    낱말 하나짜리(「숲」)는 그대로 찾는다. 여러 낱말이면 절반 넘게 보이면 들어 있다고 본다.
    """

    words = [w for w in re.findall(r"[가-힣A-Za-z]+", card) if w not in ("요", "어요")]
    if not words:
        return True
    flat = text.replace(" ", "")
    if len(words) == 1:
        stem = card_stem(card).replace(" ", "")
        return (stem[:2] if len(stem) > 2 else stem) in flat
    hit = sum(w[:2] in flat for w in words)
    return hit / len(words) >= 0.5


def card_stem(card: str) -> str:
    """카드 글자에서 이야기 안에서 찾을 줄기.

    「길을 잃었어요」 → 「길을 잃」. 아이 말처럼 길면 앞 두 낱말.
    """

    words = card.split()
    if len(words) > 3:
        return " ".join(words[:2])[:8]
    s = re.sub(r"(었어요|았어요|했어요|였어요|어요|요|어|야)[.!?]?$", "", card).strip()
    return s[:-1] if len(s) > 2 and s[-1] in "었았했였" else s


def fallback_book(
    choices: dict[str, str], pages: int, name: str | None = None, spoken: set[str] | None = None
) -> Book:
    """OpenAI 를 쓸 수 없을 때의 틀 문장 그림책. 네 칸 글자는 그대로 들어간다.

    아이가 말로 덧붙인 단계는 그 단계 첫 쪽에 아이 말을 child_words 로 표시한다.
    """

    from app.services.story_writer import josa  # 순환 import 를 피한다

    n = split_pages(pages)
    who = josa(name, "은", "는") if name else "나는"
    place, problem, action, result = (choices[s].rstrip(". ") for s in STAGES)
    texts = {
        "place": [
            f"햇살이 반짝이는 날이었어요. {who} {place}에 갔어요.",
            f"{place}에는 처음 보는 것이 가득했어요. 두근두근, 마음이 설렜어요.",
        ],
        "problem": [
            f"그런데 그때였어요! {problem}.",
            "“어떡하지?” 잠깐 멈춰 섰어요. 가슴이 콩닥콩닥 뛰었어요.",
            "그래도 포기하지 않기로 했어요. 크게 숨을 한 번 쉬었어요.",
        ],
        "action": [
            f"{who} 용기를 냈어요. 그리고 {action}.",
            "한 걸음, 또 한 걸음. 천천히 해 보았어요.",
            "그러자 조금씩 마음이 가벼워졌어요.",
        ],
        "result": [
            f"드디어! {result}.",
            "“해냈다!” 얼굴에 웃음이 활짝 피었어요.",
            f"{place}에서 보낸 하루는 정말 멋진 날이었어요.",
        ],
    }
    actions = {"place": "walk", "problem": "idle", "action": "run", "result": "dance"}
    out, k = [], 1
    for s in STAGES:
        for j in range(n[s]):
            line = texts[s][min(j, len(texts[s]) - 1)]
            child = choices[s] if j == 0 and s in (spoken or ()) else ""
            out.append(Page(k, s, actions[s] if j == 0 else "idle", [(NARRATOR, line, "")], child))
            k += 1
    order = [next(p.page for p in out if p.stage == s) for s in STAGES]
    return Book(f"{place}에 간 날", out, order, name or "")
