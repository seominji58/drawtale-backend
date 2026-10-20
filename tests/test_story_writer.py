import json

import pytest

from app.services.story_writer import (
    Line,
    count_sentences,
    has_dialogue,
    josa,
    parse_script,
    script_prompt,
    script_text,
)
from app.services.voices import NARRATOR, PROTAGONIST


def test_parse_script_and_text():
    raw = json.dumps(
        {
            "characters": [{"name": "친구", "type": "아이-남"}],
            "sentences": [
                [{"speaker": "나레이션", "text": "나는 학교에 갔어요."}],
                [
                    {"speaker": "나레이션", "text": "토토는 친구에게"},
                    {"speaker": "주인공", "text": "미안해.", "emotion": "다정"},
                    {"speaker": "나레이션", "text": "하고 말했어요."},
                ],
            ],
        },
        ensure_ascii=False,
    )
    script = parse_script(raw)
    assert script.sentences[1][1] == Line(PROTAGONIST, "미안해.", "다정")
    assert script.cast == {"친구": "아이-남"}
    assert has_dialogue(script)
    # 감정은 글에 넣지 않는다
    assert script_text(script) == "나는 학교에 갔어요. 토토는 친구에게 “미안해.” 하고 말했어요."


def test_parse_script_fills_missing_speaker_and_drops_empty():
    raw = '{"sentences": [[{"text": "나는 웃었어요."}, {"speaker": "주인공", "text": " "}], []]}'
    script = parse_script(raw)
    assert script.sentences == [[Line(NARRATOR, "나는 웃었어요.")]]
    assert script.cast == {}
    assert not has_dialogue(script)


@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"sentences": []}', '{"sentences": [[1]]}'])
def test_parse_script_rejects_bad_format(raw):
    with pytest.raises(ValueError):
        parse_script(raw)


def test_count_sentences_keeps_quotes_whole():
    # 프론트 sentences() 와 같은 결과여야 S-10 카드 수가 맞는다
    assert count_sentences("나는 친구에게 “미안해.” 하고 말했어요. 친구가 “괜찮아?” 물었어요.") == 2
    assert count_sentences("“고마워요!” 나는 집에 갔어요.") == 1
    assert count_sentences("오늘 나는 숲에 갔어요. 그런데 비가 왔어요!") == 2


def test_protagonist_name_as_speaker_is_the_protagonist():
    # GPT 가 주인공 대사 speaker 를 이름으로 적거나 characters 에 넣어도 주인공으로 본다
    raw = json.dumps(
        {
            "protagonist": "하루",
            "characters": [
                {"name": "하루", "type": "아이-여"},
                {"name": "엄마", "type": "어른-여"},
            ],
            "sentences": [[{"speaker": "하루", "text": "안녕!"}]],
        },
        ensure_ascii=False,
    )
    script = parse_script(raw)
    assert script.protagonist == "하루"
    assert script.sentences[0][0].speaker == PROTAGONIST
    assert script.cast == {"엄마": "어른-여"}
    # 아이가 지은 이름이 있으면 그 이름이 먼저다
    assert parse_script(raw, "토토").protagonist == "토토"


def test_script_prompt_is_third_person():
    prompt = script_prompt("토토")
    assert '주인공을 "토토"' in prompt
    assert '주인공은 "나"로 쓰고' not in prompt
    assert "protagonist 에 적는다" in script_prompt(None)


def test_josa_follows_final_consonant():
    assert josa("하늘", "은", "는") == "하늘은"
    assert josa("토토", "은", "는") == "토토는"
    assert josa("콩", "이", "가") == "콩이"
    assert josa("Leo", "은", "는") == "Leo는"
    assert "하늘은 학교에 갔어요" in script_prompt("하늘")
