"""OpenAI로 이야기를 만들고 부적절한 내용을 걸러낸다.

대상이 발달장애 아동이므로 문장을 짧고 쉽게 만드는 것이 가장 중요하다.
API 키가 없거나 호출이 실패하면 템플릿 문장으로 대체해 서비스가 멈추지 않게 한다.
"""

import logging

from openai import OpenAI, OpenAIError

from app.core.config import get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """너는 발달장애 아동을 위한 이야기를 쓰는 작가야.

아이가 고른 네 단계(장소, 문제, 행동, 결과)로 짧은 이야기를 만들어.

규칙:
- 정확히 네 문장으로 쓴다. 각 단계마다 한 문장씩.
- 한 문장은 15자에서 25자 사이로 짧게 쓴다.
- 초등학교 1학년이 아는 쉬운 낱말만 쓴다.
- 어려운 말, 비유, 속담은 쓰지 않는다.
- 주인공은 "나"로 쓰고, 말투는 "~어요"로 끝낸다.
- 아이가 고른 네 단계의 내용을 반드시 모두 담는다.
- 무섭거나 슬픈 결말로 끝내지 않는다.
- 이야기 본문만 쓰고 제목이나 설명은 붙이지 않는다."""

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

    return (
        f"오늘 나는 {place}에 갔어요. 그런데 {problem}. "
        f"그래서 나는 {action}. 그랬더니 {result}."
    )


class StoryWriter:
    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.client = OpenAI(api_key=api_key, timeout=timeout)

    def check_input(self, text: str) -> None:
        """아이가 입력한 내용에 부적절한 표현이 있는지 확인한다. (무료)"""

        res = self.client.moderations.create(model=MODERATION_MODEL, input=text)
        if res.results[0].flagged:
            categories = [
                name
                for name, flagged in res.results[0].categories.model_dump().items()
                if flagged
            ]
            log.warning("입력이 moderation에 걸림: %s", categories)
            raise StoryBlocked(", ".join(categories))

    def write(self, place: str, problem: str, action: str, result: str) -> str:
        user_input = USER_TEMPLATE.format(
            place=place, problem=problem, action=action, result=result
        )
        self.check_input(user_input)

        res = self.client.chat.completions.create(
            model=STORY_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            temperature=0.8,
            max_tokens=300,
        )
        text = (res.choices[0].message.content or "").strip()

        if not text:
            raise OpenAIError("빈 응답")

        # 생성된 이야기도 한 번 더 확인한다
        self.check_input(text)
        return text


def get_story_writer() -> StoryWriter | None:
    """API 키가 없으면 None을 돌려준다. 호출하는 쪽에서 템플릿으로 대체한다."""

    key = get_settings().openai_api_key
    if not key:
        return None
    return StoryWriter(key, timeout=get_settings().openai_timeout_seconds)
