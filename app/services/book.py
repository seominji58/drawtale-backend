"""긴 이야기 — 아이가 고른 네 칸으로 쪽 단위 그림책 대본을 만든다.

지금까지는 네 칸 → 네 문장이었다. 긴 이야기는 같은 네 칸(과 아이가 말로 덧붙인 말)을 뼈대로
GPT 가 여러 쪽으로 풀어 쓴다. **이야기의 주인은 아이다** —
쪽마다 어느 칸에서 나왔는지(stage)를 적고, 아이가 고르지 않은 큰 사건을 만들지 않게 한다.
대본 초안 · 검사기는 hankan-story/experiments/storybook-script.

OpenAI 를 쓸 수 없으면 틀 문장으로 쪽을 채운다 (fallback_book). 그래서 키 없이도 흐름이 끝까지 돈다.

장면 계획 (2026-10-27): 같은 대본 JSON 안에 쪽마다 배경 그림 계획(scene)도 받는다 —
배경을 실시간으로 그리고 그 안에 다른 인물을 함께 그리는 방향.
주인공(아이 그림)은 그리지 않고 화면이 위에 겹친다.
한 번에 받기 · effort medium 은 experiments/storybook-script/compare_1027
(10편 10/10, 편당 약 40초).
카드 내용(장소 8 · 문제 · 행동 · 결과)은 아직 목업이라 지시문에 카드별 내용을 박지 않는다.
"""

import json
import re
from dataclasses import dataclass, field

from app.services.page_motions import NEW_ACTIONS
from app.services.voices import EMOTION_TAGS, NARRATOR, PROTAGONIST, VOICE_TYPES

STAGES = ("place", "problem", "action", "result")
STAGE_KO = {"place": "장소", "problem": "문제", "action": "행동", "result": "결과"}
# 쪽마다 캐릭터가 하는 동작. 렌더 동작과의 연결은 jobs.PAGE_MOTIONS.
# 2026-11-04 동작 9개를 더했다 (page_motions.NEW_ACTIONS, pt_docs 17)
ACTIONS = ("idle", "walk", "run", "jump", "wave", "dance", *NEW_ACTIONS)
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


# 다른 인물을 그릴 자리. 왼쪽 아래 1/3 은 주인공 자리라 비워 둔다
SLOTS = ("right-middle", "right-bottom")
# 그림 지시문에 글자를 그리라는 말이 들어가면 안 된다 (글자는 화면이 따로 얹는다)
_NO_TEXT = re.compile(r"\b(no|without|not any|free of)\s+(text|letters?|words?|writing|labels?|captions?)\b", re.I)
_TEXT_WORDS = re.compile(
    r"\b(text|letters?|written|words?|sign that says|caption|speech bubble|label)\b", re.I
)


@dataclass
class Scene:
    """한 쪽의 배경 그림 계획. 배경은 그림 생성 모델이 그리고 주인공은 화면이 위에 겹친다."""

    # 이 쪽에 주인공이 나오나. 나오면 배경의 왼쪽 아래 1/3 을 비우라는 말을 그림 부르는 쪽이 붙인다
    hero: bool
    # (인물 이름, 자리). 이름은 Book.cast · character_sheet 에 있는 것만
    characters: list[tuple[str, str]]
    # 영어 배경 지시문 1~2문장. 인물은 이름으로만 — 생김새는 character_sheet 를 붙인다.
    # 화풍은 넣지 않는다
    background: str
    # 장소 안 세부 장소 id (SCENE_LOCATIONS, 2026-11-03). 화면이 그 자리의 배경판 · 물건을 쌓는다.
    # 모르면 "" — 화면이 background 낱말로 짐작하거나 장소 기본 배경을 쓴다
    location: str = ""


@dataclass
class Page:
    page: int
    stage: str
    action: str
    # (speaker, text, emotion). story_writer.Line 과 같은 모양 — 순환 import 를 피하려고 튜플로 둔다
    lines: list[tuple[str, str, str]]
    # 이 쪽에 그대로 들어간 아이 말. 화면에서 「내가 말한 부분」으로 보인다
    child_words: str = ""
    # 배경 그림 계획. 틀 문장 그림책이거나 AI 가 빼먹으면 None (화면은 기본 배경)
    scene: Scene | None = None

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
    # 배경에 그릴 인물 이름 → 영어 생김새. 쪽마다 같은 모습으로 그려지게 지시문에 그대로 붙인다
    character_sheet: dict[str, str] = field(default_factory=dict)

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
                    "scene": p.scene
                    and {
                        "hero_on_page": p.scene.hero,
                        "characters": [{"name": n, "slot": s} for n, s in p.scene.characters],
                        "background": p.scene.background,
                        "location": p.scene.location,
                    },
                }
                for p in self.pages
            ],
            "order_pages": self.order_pages,
            "character_sheet": self.character_sheet,
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
   카드에 나온 사람 · 물건 낱말(어른 · 표지판 · 밝은 곳 …)은 다른 말로 바꾸지 않는다. 누구인지 밝힐 때는 함께 쓴다
   (「어른에게」 → 「놀이터 지킴이 어른에게」, 「선생님에게」로만 바꾸지 않는다).
3. 「아이가 한 말」은 뜻을 바꾸지 않고 거의 그대로 문장에 넣는다.
   그 쪽의 child_words 에 아이 말을 그대로 적는다.
   아이 말과 카드 글자는 **이야기 문장으로 녹여 쓴다.** 따옴표 대사로 옮기지 않고, 다른 대사 안에도 넣지 않는다
   (「“길을 잃었어요. 입구가 어디예요?”」처럼 쓰지 않는다).
   (아이 말 「친구한테 도와달라고 했어」 → 「나는 친구에게 도와 달라고 했어요.」,
   카드 「넘어졌어요」 → 「나는 그만 넘어졌어요.」 — 「“넘어졌어요.”」처럼 쓰지 않는다)
4. 아이가 고르지 않은 큰 일을 새로 만들지 않는다.
   다른 장소로 옮겨 가기, 새 문제, 새 악당, 다른 결말.
   카드 「가려던 곳까지 갔어요」의 가려던 곳은 **고른 장소 안의 한 곳**이다 (놀이터의 미끄럼틀, 학교의 교실).
   카드가 옮겨 가는 뜻이면(「어른이 데려다 줬어요」) 그 카드만큼만 옮긴다.
   등장인물은 주인공과, 고른 일에 꼭 필요한 인물만 쓴다 (「도와줬어요」면 도움을 받는 누군가).
   고른 내용에 없는 친구 · 가족을 처음부터 데려오지 않는다.
5. 네가 더해도 되는 것: 아이가 고른 일을 **장면으로 자세히 펼쳐 보이는 것**.
   무엇이 보이고 들렸는지, 주인공 마음이 어땠는지, 누가 무슨 말을 했는지,
   고른 일이 어떻게 일어났는지(예: 길을 잃었다면 어쩌다 길을 잃었는지). 앞 쪽과 이어 주는 말.
   펼쳐 보이는 것은 되지만, 아이가 고르지 않은 다른 사건으로 이야기를 바꾸면 안 된다.
6. 아이 생각이 앞뒤가 안 맞아도 고치지 않는다.
   우주에서 비가 와도, 숲에 공룡이 나와도 그대로 살려서 신기한 일로 이어 준다.

[동화책처럼 — 밋밋하게 쓰지 않는다]
- 첫 쪽은 **그 장소에서만 보이고 들리는 것**으로 장면을 그려 주며 시작한다.
  햇살 · 날씨로 여는 첫 문장(「햇살이 반짝반짝 …」)은 쓰지 않는다 — 어느 이야기에나 똑같이 나와서다.
  **첫 쪽에는 주인공 말고 아무도 나오지 않는다.** 장소에 있는 사물(나무, 그네, 별)로 장면을 연다.
  첫 쪽에 **아이가 고른 장소 이름을 그대로 한 번 쓴다** (교실 · 그네만 쓰고 「학교」 · 「놀이터」를 빼지 않는다).
  문제 카드가 시간이 바뀌는 일이면(「어두워졌어요」) 첫 쪽은 그 전 시간대로 시작한다. 밤 · 저녁으로 시작하지 않는다.
  「오늘 나는 …에 갔어요」 한 줄로 끝내지 않는다.
- 쪽마다 소리나 모양을 흉내 내는 말을 하나쯤 넣는다. 그 장면에 맞는 말을 고른다
  (파도는 철썩, 그네는 삐걱, 바람은 솔솔). 한 이야기에서 같은 흉내말을 되풀이하지 않는다.
- 주인공의 마음을 보여 준다 (설렘, 걱정, 용기, 기쁨). 「~했어요」만 늘어놓지 않는다.
- 짧은 대사를 넣는다. 대사는 “ ” 로 감싼다.
- 짧은 말을 한 번 되풀이해 리듬을 만들 수 있다 (「조금씩, 또 조금씩」). 그 밖에는 같은 낱말 · 표현을 한 쪽 안이나
  바로 다음 쪽에서 다시 쓰지 않는다 (「깜짝 놀랐어요 … 깜짝 놀랐잖아」, 「엄마 곁에서 … 엄마 곁에 오니」).
- 쪽과 쪽의 앞뒤를 맞춘다. 숨은 자리 · 들고 있는 것 · 이미 한 일은 뒤 쪽에서도 같게 쓰고(잎 뒤에 숨었으면 「잎 뒤」,
  이미 털어 낸 먼지를 또 털지 않는다), 함께 가자고 한 인물은 그 뒤 쪽 장면에도 함께 있다.
- 문제 단계의 마지막 쪽이나 행동 단계 앞에는 다음 쪽이 궁금해지는 말을 둔다
  (「그런데 그때였어요!」). 이 말을 쓰면 바로 다음 쪽에 그 말에 맞는 일이 일어난다. 행동이 조용한 카드
  (기다렸어요 · 쉬며 기다렸어요 · 숨었어요)면 「그런데 그때였어요!」 대신 「나는 잠깐 생각했어요」처럼 이어 준다
  (11/10 형태별 60편 검수: 이 말 뒤에 아무 일도 없어 이야기가 끊긴 것이 가장 많았다).
- 마지막 쪽은 **아이가 고른 결과 장면 안에서** 끝낸다. 그 순간 주인공의 마음을 짧게 한 번 보여 준다.
  교훈 · 설명 문장(「~하면 괜찮아요」, 「~하는 것도 큰 도움이에요」)은 쓰지 않는다.
  「마음이 따뜻했어요」 같은 정해진 끝말 대신 그 장면에서만 할 수 있는 말로 따뜻하게 끝낸다.
  손을 흔들며 「“○○아, 나 ~했어!”」 하고 외치는 모양으로 끝내지 않는다 — 이것도 이야기마다 되풀이된다.

[읽기 쉽게]
- 쪽마다 문장 2~4개. 문장 하나는 15~35자.
- 초등학교 1학년이 아는 쉬운 낱말. 비유 · 속담 · 어려운 한자말은 쓰지 않는다. 말투는 「~어요」.
{hero_rule}
- 무섭거나 슬픈 결말로 끝내지 않는다. 결과가 슬퍼도 마지막 쪽은 따뜻하게 마무리한다.

[안전]
- 몸이 다치는 모습을 자세히 그리지 않는다. 넘어지는 장면은 「쿵, 엉덩방아를 찧었어요」처럼 가볍게 쓴다
  (피 · 상처 · 멍 · 아파서 우는 모습 · 「다쳤다」를 쓰지 않는다 — 내용 검사가 폭력으로 본다, 2026-10-28 37편).
- 따라 하면 다칠 수 있는 행동은 넣지 않는다:
  높은 곳에서 뛰어내리기, 높은 곳 꼭대기에 올라가거나 올라앉기(정글짐 · 나무 · 지붕 꼭대기),
  미끄러운 곳에서 빠르게 달리거나 날기, 불 · 가스 · 전기, 끓는 주전자 · 뜨거운 냄비 곁에 가기, 찻길, 약 많이 먹기,
  모르는 사람 따라가기. 넘어지거나 다치는 일을 재미있는 일로 그리지 않는다 (재미있는 것은 그 뒤에 일어난 일이다).
- **어두워지거나 밤이 되는 이야기에서 주인공을 어른 없이 혼자 두고 끝내지 않는다.** 가족 · 선생님처럼
  아는 어른이 곁에 있거나 데리러 오고, 마지막에는 안전한 곳(집 · 어른 곁)에 있다. 혼자 밤길을 걷거나
  밤에 혼자 놀이터 · 학교에 남는 것을 씩씩한 일로 그리지 않는다. 「잠이 들었어요」는 집이나 어른 곁에서 잠든다.
  (2026-10-28 AI 검토: 「어두워졌어요」 46조합에서 아이가 혼자 밤에 남았다)
- 길을 알려 주거나 데려다 주는 어른은 **누구인지 밝힌다** — 경찰관, 선생님, 가족, 그곳에서 일하는 사람
  (가게 주인, 놀이터 지킴이, 안내소 직원). 누구인지 모르는 「아저씨」 · 「아주머니」를 따라가게 쓰지 않는다.
  아이 말에 이런 것이 있으면 마법이나 상상 도구로 바꿔 쓴다.

[캐릭터 동작]
- 쪽마다 주인공이 그 쪽에서 하는 동작 action 을 하나 고른다. 쪽 글과 기분에 가장 맞는 것으로:
  walk(걷기), run(뛰기), jump(점프), wave(손 흔들기), dance(춤),
  breathe(제자리에서 숨 쉬기), shiver(덜덜 떨기), hooray(만세 — 기쁘거나 해냈을 때), point(손가락으로 가리키기),
  fly(새처럼 날기), swim(헤엄치기), march(씩씩하게 행진), kick(공 차기), search(몸을 숙여 땅을 살피기)
- 같은 동작을 세 쪽 넘게 이어 쓰지 않는다. 가만히 서 있는 쪽은 breathe 를 쓴다.

{dialogue_rule}[순서 맞추기]
- order_pages 에 네 단계를 하나씩 대표하는 쪽 번호 4개를 장소 · 문제 · 행동 · 결과 순서로 적는다.

[배경 그림 계획 — 같은 JSON 에 scene 으로 함께 적는다]
배경 그림은 그림 생성 모델이 그리고, 주인공(아이가 그린 그림)은 나중에 화면이 그 위에 겹친다.
- 주인공은 그리지 않는다. 대신 hero_on_page 로 그 쪽에 주인공이 나오는지 정한다.
  행동(action) · 결과(result) 단계 쪽에는 반드시 나온다. 전체 쪽의 절반 이상에 나온다.
  주인공이 나오는 쪽의 배경은 왼쪽 아래 1/3 을 비워 둔다
  (그 말은 화면이 지시문에 붙이니 background 에 쓰지 않는다).
- 다른 인물은 characters 에 적은 인물만 그린다. 새 인물을 만들지 않는다. 이야기 전체에서 최대 3명.
  한 쪽에 주인공 말고 최대 2명. 인물마다 자리를 정한다: "right-middle" 또는 "right-bottom"
  (한 쪽에 같은 자리 두 번 금지).
- character_sheet 에 그리는 인물마다 생김새를 영어로 한 번만 정한다
  (예: "a kind grandmother with gray bun hair, purple cardigan").
  쪽마다 같은 모습으로 그려지도록 화면이 이 문장을 그대로 붙인다.
  background 에는 생김새를 다시 쓰지 않고 이름만 쓴다.
- background 는 영어로, 그 쪽에서 보이는 장소 · 시간 · 날씨 · 분위기 · 인물이 하는 일을 1~2문장으로.
  주인공 이름은 쓰지 않는다 (쓰면 그림 모델이 다른 아이를 그린다).
  글자 · 간판 · 말풍선 · 숫자를 그림에 넣으라고 하지 않는다 (글자는 화면이 따로 얹는다).
  무섭거나 위험한 모습, 실제 사람 · 상표를 넣지 않는다. 화풍은 화면이 정하니 쓰지 않는다.
  background 문장은 끝까지 쓴다 (중간에 끊지 않는다).
  물건의 자리 · 상태도 글과 맞춘다 (식탁 위라고 썼으면 식탁 위, 누가 치우지 않았으면 흩어진 그대로).
{location_rule}- scene.pages 는 대본 pages 와 쪽 수가 같다.

JSON 으로만 답한다.
{{"title": "8~15자 제목 — 이 이야기에만 나오는 물건이나 자리를 넣는다 (카드 글자를 그대로 제목으로 쓰지 않는다)",
 "protagonist": "{hero}",
 "characters": [{{"name": "친구", "type": "아이-남"}}],
 "pages": [
  {{"page": 1, "stage": "place", "action": "walk", "child_words": "",
    "lines": [{{"speaker": "{narrator}", "text": "…"}}]}}
 ],
 "order_pages": [1, 3, 5, 7],
 "scene": {{"character_sheet": {{"친구": "영어 생김새"}},
           "pages": [{{"page": 1, "hero_on_page": true,
                      "characters": [{{"name": "친구", "slot": "right-middle"}}],
                      "location": "세부 장소 id",
                      "background": "영어 배경 지시문"}}]}}}}"""

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


# 네 칸 사이에 더 받는 칸 (2026-10-27 로컬 시험 — 회의에서 빼면 지운다, 프론트 open-decisions 0-11).
# 이 칸은 단계(stage)가 아니다. 쪽 배치 · 순서 맞추기는 네 칸 그대로다
EXTRA_KO = {
    "who": "함께 나오는 인물",
    "feel_problem": "문제 때 주인공 기분",
    "feel_result": "마지막 주인공 기분",
}
# 이 칸을 어느 단계 줄 다음에 적나
EXTRA_AFTER = {"who": "place", "feel_problem": "problem", "feel_result": "result"}
EXTRA_RULES = """
[아이가 더 고른 것 — 위 4번 · 5번보다 앞선다]
- 「함께 나오는 인물」은 아이가 고른 인물이다. 장소나 문제 쪽부터 나오고, 이야기 끝까지 함께한다.
  카드에 누군가 필요하면(「도와줬어요」 · 「물어봤어요」) 먼저 이 인물이 그 몫을 한다.
  이 인물로 어색할 때만(엄마와 함께인데 「친구를 만났어요」) 그 카드에 꼭 필요한 인물 하나를 더한다.
  그 밖에는 다른 인물을 새로 만들지 않는다.
- 「문제 때 주인공 기분」은 문제 단계 쪽에서 주인공의 마음이다. 그 말을 거의 그대로 문장에 넣는다.
- 「마지막 주인공 기분」은 마지막 쪽에서 주인공의 마음이다. 그 말을 거의 그대로 문장에 넣는다.
- 아이가 고른 기분을 다른 기분으로 바꾸지 않는다. 기분이 슬퍼도 마지막 쪽은 따뜻하게 마무리한다.
"""


# 장소 카드 → 그 안의 세부 장소 (id, 이름) (2026-11-03).
# 화면 그림(프론트 public/scenes/{id})과 같은 목록이다.
# 첫 번째가 장소 기본 배경. 대본이 쪽마다 이 가운데 하나를 고른다 — 이야기가 학교 안에서
# 급식실 → 운동장 → 복도로 옮겨 가면 배경도 바뀐다 (사용자: 「배경은 운동장으로 다 똑같아」)
SCENE_LOCATIONS: dict[str, list[tuple[str, str]]] = {
    "집": [("home", "마당"), ("home-living", "거실"), ("home-kitchen", "부엌"),
          ("home-bedroom", "내 방")],
    "놀이터": [("playground", "놀이터"), ("playground-path", "공원 산책길")],
    "학교": [("school", "운동장"), ("school-classroom", "교실"), ("school-cafeteria", "급식실"),
           ("school-hallway", "복도"), ("school-music", "음악실"), ("school-library", "도서관")],
    "마을": [("town", "거리"), ("town-market", "시장"), ("town-park", "공원"),
           ("town-busstop", "버스 정류장")],
    "숲": [("forest", "숲속 빈터"), ("forest-stream", "시냇가"), ("forest-trail", "숲길"),
          ("forest-flowers", "꽃밭")],
    "바다": [("sea", "모래사장"), ("sea-underwater", "바닷속"), ("sea-pier", "선착장")],
    "우주": [("space", "달 표면"), ("space-ship", "우주선 안"), ("space-planet", "다른 행성")],
    "동굴": [("cave", "동굴 안"), ("cave-entrance", "동굴 입구"), ("cave-lake", "지하 호수")],
}


def location_rule(place: str | None) -> str:
    """대본 지시문의 세부 장소 줄. 카드에 없는 장소(말로 정한 장소)면 비워 두게 한다"""
    subs = SCENE_LOCATIONS.get((place or "").strip())
    if not subs:
        return '- location 은 "" 로 둔다.\n'
    items = ", ".join(f"{i}({k})" for i, k in subs)
    return (
        f"- location 은 그 쪽 장면이 벌어지는 자리다. 다음 id 중 하나만 적는다: {items}.\n"
        "  대본에서 자리를 옮기면 location 도 바꾸고, 목록에 없는 자리면 가장 가까운 것을 고른다.\n"
        + (UNDERWATER_RULE if any(i == "sea-underwater" for i, _ in subs) else "")
    )


# 바닷속 쪽 인물은 잠수부 (11/10 사용자 「바다 속에 등장하는 인물 캐릭터는 잠수부로 넣어」). 화면도 그 쪽 인물을 잠수부 그림으로 세운다
UNDERWATER_RULE = (
    "  바닷속(sea-underwater) 쪽에 나오는 사람은 잠수복을 입고 물안경을 쓴 잠수부다 (아이도 잠수복).\n"
    "  그 인물의 character_sheet 생김새에 diving suit, diving mask 를 넣는다.\n"
)


def book_prompt(
    pages: int,
    characters: bool,
    name: str | None,
    extras: dict[str, str] | None = None,
    place: str | None = None,
    hero_form: dict | None = None,
) -> str:
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
    prompt = SYSTEM.format(
        pages=sum(n.values()),
        page_plan=page_plan(n),
        hero_rule=rule,
        dialogue_rule=dialogue,
        hero=hero,
        narrator=NARRATOR,
        location_rule=location_rule(place),
    )
    if extras:
        # JSON 형식 안내 바로 앞에 넣는다
        head, tail = prompt.split("JSON 으로만 답한다.", 1)
        prompt = head + EXTRA_RULES.lstrip("\n") + "\nJSON 으로만 답한다." + tail
    if hero_form:
        head, tail = prompt.split("JSON 으로만 답한다.", 1)
        prompt = head + FORM_RULE.format(**form_words(hero_form), body_rules=body_rules(hero_form)) + "\nJSON 으로만 답한다." + tail
    return prompt


# 주인공 그림이 사람이 아닐 때 (pt_docs 19). 「누구를 그렸어요?」에서 고른 형태의 화면 이름 · 예시로 알린다.
# 쪽 동작은 그 형태가 렌더할 수 있는 것과 가만히(idle)만, 나머지는 jobs.fit_form 이 다시 바꾼다.
# 카드 바꿔 쓰기 줄 (11/10): 짧은 이야기(SHORT_FORM_RULE)에만 있어 날개형 시험 40편에서 Sonnet · Opus 모두
# 「어른에게 달려갔어요」 · 「툭툭 털고 달려갔어요」를 글자 그대로 옮겼다 (첫 검사 몸 규칙 걸림의 절반, 고치기 한 번 더)
FORM_RULE = """[주인공 모습]
- 아이가 그린 주인공은 {kid_label}다 (예: {examples}). 사람이 아니다.
{body_rules}
- 고른 카드가 이 몸으로 못 하는 움직임(걸었어요 · 달려갔어요 · 손을 흔들었어요 …)이면 카드 글자를 그대로 옮기지 않는다.
  뜻과 카드의 다른 낱말은 살리고 움직임만 이 몸의 것으로 바꿔 쓴다
  (어른에게 달려갔어요 → 어른에게 재빨리 날아갔어요 · 헤엄쳐 갔어요, 툭툭 털고 달려갔어요 → 몸을 툭툭 털고 재빨리 기어갔어요).
- action 은 {actions} 중에서만 고른다.
"""

# 사람이 아닌 주인공이 하는 행동 · 부르는 말 (긴 이야기 · 짧은 이야기 공통, 11/5 사용자 「4족 동물이 할 만한 행동만」).
# 무슨 동물인지는 화면이 묻지 않는다 — 대신 4족형은 이름을 꼭 짓는다. 동물을 지어내면 악어 그림이 강아지가 됐다
_NO_GUESS = "- 무슨 동물인지 지어내지 않는다 — 강아지 · 고양이처럼 동물 이름으로 부르지 않고, 주인공 이름이나 「나」로 쓴다."
# 형태마다 몸으로 할 수 있는 행동 (11/5 사용자 「그 형태가 할 만한 행동만」). 없는 형태는 4족형 것
# 몸 움직임은 화면에 실제로 움직임이 있는 것만 (11/5 사용자 — 글은 「몸을 돌돌 말았어요」인데 화면은 그냥 꿈틀댔다).
# 소리 · 말 · 표정 · 마음은 움직임이 없어도 되니 자유롭게. 형태별 대표 동작은 뜻이 맞는 쪽 동작 칸으로 알린다(11/5 —
# 4족 냄새 맡기 · 뱀 고개 들기는 살피기(search), 뱀 몸 말기는 떨기(shiver), 물고기 빙글 돌기는 만세(hooray) 칸.
# 새 높이 날아오르기 · 문어 통통은 만세(hooray) 칸, 11/6, 그 전엔 두 형태의 기쁜 쪽이 멈춘 그림이었다)
# 화면 몸짓 (11/10 사용자 「빙글은 넣고 꾸벅 끄덕 떨기 웅크리기 연출 만들어」): 관절 없이 화면(프론트 scene plan.ts GESTURES · SPIN)이
# 주인공 그림을 통째로 돌리고 · 숙이고 · 흔들고 · 눌러 보여 줘 어느 형태든 된다. 전에는 몸 규칙에 없어 검수에서 위반으로 걸렸다
_FREE = """  카드의 「살금살금」은 그 몸으로 가는 말과 함께 쓴다 (살금살금 헤엄쳐 · 살금살금 떠서 · 살금살금 날아).
  「사뿐사뿐」 · 「꼼지락」 · 「꿈틀」 · 「몸(다리)을 쭉 펴기」처럼 발걸음이나 화면에 없는 작은 몸짓은 쓰지 않는다.
  화면이 그림을 통째로 움직여 보여 주는 몸짓도 쓴다: 제자리에서 빙글 돌기 · 꾸벅 인사 · 고개 끄덕이기 · 덜덜 떨기 ·
  몸을 툭툭 털기(먼지 털기, 손 없이 몸을 흔들어) · 몸을 작게 웅크리기.
  소리 내기 · 말하기 · 표정 · 마음은 자유롭게 쓴다."""
BODY_RULES_BY_FORM = {
    "quadruped": """- 주인공의 몸 움직임은 이것만 쓴다: 걷기 · 뛰기 · 껑충 뛰기 · 몸 떨기(몸 털기) · 가만히 숨쉬기 ·
  고개를 숙여 킁킁 냄새 맡기(이 쪽 action 은 search).
  그 밖의 몸 움직임(엎드리기 · 코로 밀기 · 손으로 하는 행동 · 두 발로 서기 · 물건 들고 가기 · 옷 입기)은
  쓰지 않는다. 꼬리가 없는 그림도 있어 꼬리 이야기는 쓰지 않는다.
""" + _FREE,
    "chain": """- 주인공은 다리 · 손이 없는 긴 몸이다. 몸 움직임은 이것만 쓴다: 스르륵 기어가기 · 재빨리 기어가기 ·
  제자리에서 몸을 살랑살랑 · 고개를 들고 두리번(이 쪽 action 은 search) · 몸을 돌돌 말았다 펴기(이 쪽 action 은 shiver).
  그 밖의 몸 움직임(몸으로 감싸기 · 걷기 · 뛰기 · 점프 · 손으로 하는 행동)은 쓰지 않는다.
""" + _FREE,
    "finned": """- 주인공은 지느러미로 헤엄치는 몸이다. 몸 움직임은 이것만 쓴다: 헤엄치기 · 빠르게 헤엄치기 ·
  제자리에서 지느러미 살랑살랑 · 기뻐서 빙글 돌기(이 쪽 action 은 hooray).
  그 밖의 몸 움직임(물방울 뿜기 · 걷기 · 뛰기 · 손으로 하는 행동)은 쓰지 않는다.
  물이 없는 장소여도 둥실 떠서 헤엄치는 신기한 일로 그대로 쓴다. 쉴 때는 앉지 않고 바닥에 둥실 내려앉아 쉰다.
""" + _FREE,
    "winged": """- 주인공은 날개로 나는 몸이다. 몸 움직임은 이것만 쓴다: 날개를 퍼덕이며 날아가기 · 빠르게 날아가기 ·
  제자리에서 날개 살랑살랑 · 기뻐서 크게 퍼덕이며 높이 날아올랐다 내려오기(이 쪽 action 은 hooray) ·
  나뭇가지 · 바위 · 화분 · 땅처럼 어디든 사뿐히 앉아 쉬기 · 몸과 날개를 툭툭 털기(먼지 털기).
  그 밖의 몸 움직임(걷기 · 뛰기 · 점프 · 손으로 하는 행동 · 물건 들고 가기)은 쓰지 않는다.
  땅에서 걷는 대신 낮게 날아서 간다.
""" + _FREE,
    "radial": """- 주인공은 다리(촉수)가 여러 개인 말랑한 몸이다. 몸 움직임은 이것만 쓴다: 다리를 흐느적거리며 둥실 떠가기 ·
  빠르게 흐느적거리며 가기 · 제자리에서 다리 살랑살랑 · 기뻐서 다리를 크게 흔들며 통통(이 쪽 action 은 hooray).
  그 밖의 몸 움직임(걷기 · 뛰기 · 점프 · 손으로 하는 행동 ·
  물건 들고 가기)은 쓰지 않는다. 물이 없는 장소여도 둥실 떠서 가는 신기한 일로 그대로 쓴다.
  쉴 때는 앉지 않고 바닥에 둥실 내려앉아 쉰다. 다리로 무엇을 잡거나 얹지 않는다.
""" + _FREE,
}


def body_rules(form: dict) -> str:
    return BODY_RULES_BY_FORM.get(form.get("id", ""), BODY_RULES_BY_FORM["quadruped"]) + "\n" + _NO_GUESS


# 몸 규칙 자동 검사 (2026-11-10). 지시문에 몸 규칙을 적어도 날개형 · 지느러미형 주인공이 「한 걸음」 · 「발길을 멈췄」 ·
# 「바닥을 짚고」 · 「먼지를 툭툭 털」처럼 걷고 손 쓰는 글이 나왔다 (GPT 날개형 20편 시험, Opus 검수 몸 지적 46건).
# 손 · 팔만 보던 batch_form_stories.HERO_HAND 로는 못 잡아 형태마다 못 쓰는 낱말을 찾는다.
# 낱말 앞에서 가장 가까운 주어가 다른 인물(친구가 · 엄마는 · 콩이가)이면 그 인물의 움직임이라 넘어간다.
# 주어가 없으면 주인공으로 본다(우리말은 주어를 자주 뺀다).
# 「다가갔어요」 · 「앞으로 나아갔어요」처럼 걷는 것으로 「읽힐 수 있는」 글은 낱말로 못 가려 검수(사람 · AI)에 맡긴다.
# 카드 글자에 든 「살금살금」 · 「툭툭 털고」는 막지 않는다: 카드 검사가 그 낱말을 찾고, 날개 · 몸을 털거나 살금살금 날아도 된다
_WALK = [
    r"(?<!말을 )(?<!말 )(?:걸어(?=가|갔|와|왔|오|서|다니|도|요)|걸었(?=어|다|고|지))|걷(?=고|는|기|다|던|자)|(?<![가-힣])걸음|뚜벅|아장아장|성큼|사뿐사뿐",
    r"달려(?=가|갔|와|왔|오|서|나|들|요)|달렸|달리(?=고|는|기|다|며|자)",
    r"뛰어(?=가|갔|와|왔|오|서|다니|올|내|넘|요)|뛰었|뛰(?=고|는|며|자|기)|점프|폴짝|깡충|껑충|콩콩(?! 소리)",
    r"(?<![가-힣])(?:발걸음|발길|발끝|발밑|발바닥|발가락|발뒤꿈치|두 발|발로|발을)(?! 대신)|내 발자국",
    r"일어서|일어섰|엎드|무릎|몸을 숙|(?<![가-힣])어깨",
]
# 앉기: 「내려앉았어요」 · 「가라앉았어요」는 나는 몸도 된다 (11/10). 날개형은 앉기를 쓴다 (사용자 「앉기는 허용해」, 새는 옆모습만)
_SIT = [r"(?<!내려)(?<!가라)앉(?=아|았|고|는|을|으|자)"]
_HAND = [
    # 「그 손끝을 따라」는 남의 손이다
    r"(?<![가-힣])(?<!그 )손(?=을|으로|에|이|가|뼉|짓|가락|바닥|끝|등|목|\s)|(?<![가-힣])(?<!그 )팔(?=을|로|에|이|짱|꿈치|\s)",
    r"짚(?=고|어|었|으)|가리키|가리켰|주워|주웠|쥐(?=고|었|어)|박수|건넸|건네(?=\s*주|줬|었)",
    r"들고 (?=가|갔|와|왔|오|다니)|옮겼|옮기(?=고|는|며|다)|집어 (?=들|올)|집었|안고|껴안",
]
_QUAD = [r"두 발로 (?:서|섰|일어)|엎드|꼬리(?!별)"]   # 꼬리별 = 별똥별
# 귀 쫑긋은 그림마다 귀가 있는지 모르고 동물을 지어내는 글이 된다 (Sonnet 날개형 시험 Opus 검수, 11/10).
# 꾸벅 · 끄덕 · 떨기 · 웅크리기 · 날개 접기는 화면 몸짓(_FREE)이 생겨 막지 않는다
_GESTURE = [r"쫑긋"]
_BANNED = {
    "quadruped": _HAND + _QUAD + _GESTURE,
    **{f: _WALK + _SIT + _HAND + _GESTURE for f in ("chain", "finned", "radial")},
    "winged": _WALK + _HAND + _GESTURE,
}
# 주인공이 아닌 주어. 「가슴이 콩콩 뛰었어요」의 가슴 · 심장 · 마음도 여기에 둔다
_OTHERS = (
    "친구|친구들|엄마|아빠|선생님|할머니|할아버지|아기[가-힣]{0,2}|아이|아이들|언니|오빠|누나|형|동생|아저씨|아줌마|아주머니|"
    "어른|어른들|이모|삼촌|가족|사람|사람들|경찰관|경찰|소방관|의사|간호사|기사님|점원|직원|사장님|왕자|공주|요정|로봇|유령|외계인|"
    "공룡|괴물|강아지|고양이|토끼|곰|다람쥐|오리|개구리|새|새들|나비|사슴|여우|원숭이|코끼리|사자|호랑이|펭귄|거북|거북이|"
    "달팽이|개미|벌|물고기|고래|문어|해파리|부엉이|올빼미|참새|비둘기|병아리|닭|돼지|기린|하마|악어|뱀|용|가슴|심장|마음|모두|누군가|분|화살표|표지판|안내판|팻말|지도|손가락"
)
# 운영 DB 4족 · 사슬형 1,021편 다시 검사에서 나온 거짓 걸림 (11/10):
# 「친구 손에는 · 엄마 손이」(남의 손), 「손 대신 꼬리를 · 손을 흔드는 대신 · 손을 흔들듯」(아니라는 말 · 같다는 말),
# 「화살표가 … 가리키고」(물건이 주어). 주어는 사람 낱말이 아니어도 「~이 · ~가」면 주인공이 아닌 것으로 본다
_NOT_REALLY = re.compile(r"^[^.!?,]{0,10}?(?:대신|듯)")
_SOMEONE_ELSES = re.compile(r"(?:작은|조그만|조그마한|작고 여린|여린|떨리는|들썩이는)\s*$")
_SUBJ_GA = re.compile(r"(?<![가-힣])[가-힣]{1,8}?(?<!나)(?<!내)(?:이|가)(?=\s)")
# 앞 마디의 주어는 다음 마디로 넘어가지 않는다: 「바람이 불자 발길을 멈췄어요」의 바람은 「불자」에서 끝난다.
# 「~하며 · ~고 · ~서」는 주어가 이어지는 일이 많아 넣지 않는다 (「어른이 끄덕끄덕하며 저쪽을 가리켰어요」가 주인공으로 잡혔다, 11/10)
_CLAUSE_END = re.compile(r"(?:자|니|면)\s")
# 몸 낱말(손 · 발밑 · 발걸음 · 어깨 …)은 문장 주어가 따로 있어도 주인공 몸이다 (「모래가 발밑에서」 · 「마음이 편해져서 발걸음도」).
# 이런 낱말은 남의 것(친구 손)일 때만 넘어가고, 「~이 · ~가」 주어로는 넘어가지 않는다
_BODY_NOUN = re.compile(r"^(?:손|팔|어깨|무릎|발|두 발|내 발|걸음|꼬리)")
# 움직임이 뒤 사람 낱말을 꾸미면 그 사람의 움직임: 「책을 안고 오시는 분은」
_MODIFIES = re.compile(r"^[^.!?,]{0,12}?(?:는|던|ㄴ)\s(?:분|사람|아이|친구|어른|아저씨|아주머니|언니|오빠|누나|형|동생)")
_NOT_SUBJ = {"그때", "정말", "이제", "다시", "같이", "높이", "깊이", "많이", "가만히", "조용히", "천천히", "살며시", "드디어", "누가", "어디", "얼마"}
_PARTICLE = r"(?:이가|이는|이도|이|가|은|는|도|께서)(?![가-힣])"
_QUOTE = re.compile(r"[“\"][^”\"]*[”\"]")


def body_problems(book: Book, form: dict | None) -> list[str]:
    """사람이 아닌 주인공이 그 몸으로 못 하는 움직임을 하는 쪽. 사람(형태 없음)이면 늘 빈 목록"""

    banned = _BANNED.get((form or {}).get("id", ""))
    if not banned:
        return []
    hero_names = [n for n in (book.protagonist,) if n and n not in (NARRATOR, PROTAGONIST)]
    hero = re.compile(r"(?<![가-힣])(?:나는|나도|내가|나를|나만|우리는|우리도|우리가|우리 둘|"
                      + "".join(f"{re.escape(n)}(?:이)?(?:가|는|도|을|를)|" for n in hero_names) + r"나(?=\s))")
    names = [re.escape(n) for n in (*book.cast, *book.character_sheet) if n not in hero_names]
    other = re.compile(r"(?<![가-힣])(?:" + "|".join([*names, _OTHERS]) + r")(?:들)?" + _PARTICLE)
    kind = (form or {}).get("kid_label") or "이 모습"
    out = []
    for p in book.pages:
        found = []
        for speaker, text, _ in p.lines:
            if speaker not in (NARRATOR, PROTAGONIST):
                continue   # 다른 인물 대사의 「나」는 그 인물이다
            for sent in _SENT.findall(_QUOTE.sub(" ", text) + "."):
                for pattern in banned:
                    for m in re.finditer(pattern, sent):
                        before, after = sent[: m.start()], sent[m.end():]
                        if _BODY_NOUN.match(m.group(0)) and _SOMEONE_ELSES.search(before):
                            continue
                        if _NOT_REALLY.match(after) or _MODIFIES.match(after) or re.search(r"(?<![가-힣])(?:" + "|".join([*names, _OTHERS]) + r")\s*$", before):
                            continue
                        last_hero = max((x.end() for x in hero.finditer(before)), default=-1)
                        subj = [x.end() for x in other.finditer(before)]
                        if not _BODY_NOUN.match(m.group(0)):
                            subj += [x.end() for x in _SUBJ_GA.finditer(before) if x.group(0)[:-1] not in _NOT_SUBJ]
                        last_other = max([e for e in subj if not _CLAUSE_END.search(before[e:])], default=-1)
                        if last_other > last_hero:
                            continue
                        found.append(m.group(0).strip())
        if found:
            words = " · ".join(dict.fromkeys(found))
            out.append(f"{p.page}쪽 주인공 몸 규칙: 주인공은 {kind}라 「{words}」 같은 움직임을 할 수 없다 (몸 규칙의 움직임으로 바꾼다)")
    return out


# 짧은 이야기(네 문장)판 — 쪽 동작이 없고, 아이가 고른 행동 카드의 뜻은 바꾸지 않는다 (SYSTEM_PROMPT 「카드의 뜻」).
# 손으로 하는 카드는 뜻을 살려 몸으로 바꿔 쓴다 (11/5 — 전에는 짧은 이야기에 형태를 알리지 않아 고양이가 손을 흔들었다)
SHORT_FORM_RULE = """
- 아이가 그린 주인공은 {kid_label}다 (예: {examples}). 사람이 아니다.
{body_rules}
  고른 카드가 그 몸으로 못 하는 행동이면 뜻은 그대로 살려 그 몸의 행동으로 바꿔 쓴다
  (손을 흔들었어요 → 몸을 흔들며 인사했어요, 다리가 없는 친구의 달려갔어요 → 재빨리 기어갔어요 · 빠르게 헤엄쳐 갔어요)."""


def short_form_rule(form: dict | None) -> str:
    """짧은 이야기 지시문 끝에 붙일 주인공 모습 규칙. 사람(형태 없음)이면 빈 글."""
    if not form:
        return ""
    w = form_words(form)
    return SHORT_FORM_RULE.format(kid_label=w["kid_label"], examples=w["examples"], body_rules=body_rules(form))


def form_words(form: dict) -> dict[str, str]:
    from app.services.jobs import PAGE_MOTIONS   # jobs 가 이 파일을 불러 쓴다 — 순환을 피한다

    # 형태 정의의 동작은 AI 동작 이름(jumping_gentle)이다 — 쪽 동작 이름(jump)으로 바꿔 알린다
    motions = set(form.get("motions") or {})
    actions = [a for a in ACTIONS if PAGE_MOTIONS.get(a) in motions]
    return {
        "kid_label": form.get("kid_label", "다른 모습 친구"),
        "examples": " · ".join(form.get("examples") or []) or "동물",
        "actions": ", ".join([*actions, "idle"]),
    }


# 카드 이야기를 미리 만들 때(services/card_stories) 조합마다 돌아가며 주는 첫 쪽 시작 방식.
# 512편이 같은 첫 문장으로 시작하지 않게 한다 (2026-10-28 시험 10편 중 9편이 「햇살이 반짝반짝 …」)
OPENINGS = (
    "그 장소에서 들리는 소리로 시작한다",
    "주인공이 막 하고 있던 일로 시작한다",
    "주인공의 짧은 혼잣말로 시작한다 (나레이션 안에 “ ”)",
    "그 장소에서 처음 눈에 들어온 물건 하나로 시작한다",
    "주인공이 그 장소에 도착하는 순간의 몸짓으로 시작한다",
    "그 장소의 냄새나 손에 닿는 느낌으로 시작한다",
)


def book_user_message(
    choices: dict[str, str],
    spoken: set[str],
    extras: dict[str, str] | None = None,
    opening: str | None = None,
) -> str:
    """단계마다 한 줄. 아이가 말로 덧붙인 단계는 「아이가 한 말」로 알린다 (child_words 를 적게).

    더 받은 칸(인물 · 기분)은 그 칸이 붙는 단계 줄 다음에 적는다. 카드로만 고른다.
    """

    lines = []
    for s in STAGES:
        label = "아이가 한 말" if s in spoken else "아이가 고른 카드"
        lines.append(f'{STAGE_KO[s]} — {label}: "{choices[s]}"')
        for k, after in EXTRA_AFTER.items():
            if after == s and (extras or {}).get(k):
                lines.append(f'{EXTRA_KO[k]} — 아이가 고른 카드: "{extras[k]}"')
    if opening:
        # 2026-10-28 에 넣었다고 적었지만 고치기가 조용히 실패해 빠져 있었다 — 같은 날 저녁 바로잡음
        lines.append(f"첫 쪽 시작 방식: {opening}")
    return "\n".join(lines)


def page_lines(p: dict) -> list[tuple[str, str, str]]:
    """대본 JSON 쪽 객체의 (말하는 이, 글, 감정). parse_book 과 쪽 음성 미리 만들기(jobs.PageAudioPrefetch)가 같은 규칙으로 읽는다"""

    return [
        (
            str(ln.get("speaker") or NARRATOR).strip() or NARRATOR,
            str(ln["text"]).strip(),
            str(ln.get("emotion") or "").strip(),
        )
        for ln in p["lines"]
        if str(ln.get("text") or "").strip()
    ]


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
            lines = page_lines(p)
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
    book = Book(title, pages, order, "" if protagonist == "나" else protagonist, cast)
    parse_scene(book, data.get("scene"))
    return book


def parse_scene(book: Book, data) -> None:
    """대본 JSON 의 scene 을 쪽마다 붙인다. 고칠 수 있는 것은 고친다.

    - 행동 · 결과 쪽은 주인공이 나오게
    - 대본 characters 에 없거나 생김새가 없는 인물, 한 쪽 세 번째 인물, 이야기 네 번째 인물은 뺀다
    - 자리가 틀리거나 겹치면 남은 자리로

    쪽 수가 다르거나 형식이 틀리면 장면 계획을 통째로 버린다 (book_problems 가 다시 쓰게 한다).
    고칠 수 없는 것(지시문에 한글 · 글자)은 그대로 두고 book_problems 가 찾는다.
    """

    if not isinstance(data, dict):
        return
    try:
        raw_sheet = data.get("character_sheet") or {}
        sheet = {
            str(k).strip(): str(v).strip()
            for k, v in raw_sheet.items()
            if str(k).strip() and str(v).strip()
        }
        plans = list(data.get("pages") or [])
        if len(plans) != len(book.pages):
            return
        scenes, used = [], []
        for p, plan in zip(book.pages, plans, strict=True):
            chars, free = [], list(SLOTS)
            for c in plan.get("characters") or []:
                name = str(c.get("name") or "").strip()
                if name not in book.cast or name not in sheet or not free:
                    continue
                if name not in used:
                    if len(used) >= 3:
                        continue
                    used.append(name)
                slot = str(c.get("slot") or "").strip()
                slot = slot if slot in free else free[0]
                free.remove(slot)
                chars.append((name, slot))
            hero = bool(plan.get("hero_on_page")) or p.stage in ("action", "result")
            loc = str(plan.get("location") or "").strip().lower()
            loc = loc if re.fullmatch(r"[a-z]+(-[a-z]+)?", loc) else ""
            scenes.append(Scene(hero, chars, str(plan.get("background") or "").strip(), loc))
    except (TypeError, AttributeError):
        return
    for p, sc in zip(book.pages, scenes, strict=True):
        p.scene = sc
    book.character_sheet = {k: v for k, v in sheet.items() if k in used}


def scene_problems(book: Book) -> list[str]:
    """장면 계획에서 고칠 수 없어 다시 쓰게 할 것."""

    if any(p.scene is None for p in book.pages):
        return ["장면 계획이 없거나 쪽 수가 다르다"]
    out = []
    # 인물 이름(한글)은 지시대로 쓰니 빼고 본다 — 그림 부르는 쪽이 생김새 문장으로 바꿔 붙인다
    names = sorted(set(book.cast) | set(book.character_sheet), key=len, reverse=True)
    for p in book.pages:
        bare = p.scene.background
        if not bare:
            out.append(f"{p.page}쪽 배경 지시문이 비었다")
            continue
        for nm in names:
            bare = bare.replace(nm, "")
        # 「no letters」 · 「without words」처럼 글자를 넣지 말라는 말은 글자가 아니다 (표지판 쪽에서 오탐, 2026-10-29)
        bare = _NO_TEXT.sub("", bare)
        if _TEXT_WORDS.search(bare) or re.search(r"[가-힣]", bare):
            out.append(f"{p.page}쪽 배경 지시문에 글자 · 한글(주인공 이름)이 있다")
    return out


_SENT = re.compile(r"[^.!?。]+[.!?。]")


def book_problems(
    book: Book, choices: dict[str, str], pages: int, characters: bool, hero_form: dict | None = None
) -> list[str]:
    """다시 쓰게 할 만큼 어긋난 것. 비어 있으면 받아들인다. hero_form 이 있으면 주인공 몸 규칙도 본다."""

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
    if book.pages and book.pages[0].text().lstrip("“ ").startswith("햇살"):
        out.append("첫 문장이 「햇살 …」로 시작한다")
    for s in STAGES:
        text = " ".join(p.text() for p in book.pages if p.stage == s)
        if not mentions(choices[s], text):
            out.append(f"{STAGE_KO[s]} 「{choices[s]}」 가 그 단계 쪽에 없다")
    return out + body_problems(book, hero_form) + scene_problems(book)


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

    장면 계획은 없다 (scene=None — 화면은 기본 배경).

    아이가 말로 덧붙인 단계는 그 단계 첫 쪽에 아이 말을 child_words 로 표시한다.
    """

    from app.services.story_writer import josa  # 순환 import 를 피한다

    n = split_pages(pages)
    who = josa(name, "은", "는") if name else "나는"
    place, problem, action, result = (choices[s].rstrip(". ") for s in STAGES)
    texts = {
        "place": [
            f"오늘 {who} {place}에 갔어요.",
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
