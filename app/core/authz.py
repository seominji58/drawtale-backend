"""누가 무엇을 볼 수 있는지 정한다.

그림과 이야기에는 아이가 그린 것이 담긴다. 주소(UUID)를 아는 것만으로 남의 것을
보거나 고칠 수 있으면 안 된다.

규칙은 하나다. **자료에 주인이 있으면 그 주인만 손댈 수 있다.**

- 로그인하고 올린 그림 → `user_id` 가 찍힌다. 그 계정만 접근한다
- 로그인하지 않고 올린 그림 → 주인이 없다. 주소를 아는 사람이 쓴다
  (아이는 계정 없이도 끝까지 갈 수 있어야 한다 — F-ACC-01)

남의 것이면 403이 아니라 **404** 를 돌려준다. 403 은 「그 자료가 있긴 하다」를
알려주는 셈이기 때문이다.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Character, Story, User


def _deny() -> AppError:
    return AppError("CHARACTER_NOT_FOUND", "캐릭터를 찾을 수 없어요.", 404)


def owned_character(db: Session, character_id: uuid.UUID, user: User | None) -> Character:
    """캐릭터를 가져오되, 남의 것이면 없는 것처럼 답한다."""

    character = db.get(Character, character_id)
    if character is None:
        raise _deny()
    if character.user_id is not None and (user is None or character.user_id != user.id):
        raise _deny()
    return character


def owned_story(db: Session, story_id: uuid.UUID, user: User | None) -> Story:
    """이야기는 그림을 따라간다. 그림 주인이 이야기 주인이다."""

    story = db.get(Story, story_id)
    if story is None:
        raise AppError("STORY_NOT_FOUND", "이야기를 찾을 수 없어요.", 404)
    character = db.get(Character, story.character_id)
    if character is not None and character.user_id is not None:
        if user is None or character.user_id != user.id:
            raise AppError("STORY_NOT_FOUND", "이야기를 찾을 수 없어요.", 404)
    return story
