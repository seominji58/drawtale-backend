from app.services.voices import NARRATOR, PROTAGONIST, VOICE_TYPES, cast_voices, tagged


def test_cast_by_type_without_overlap():
    voices = cast_voices({"엄마": "어른-여", "개구리": "작은 동물"}, "boy", "narrator")
    assert voices[NARRATOR] == "narrator"
    assert voices[PROTAGONIST] == VOICE_TYPES["아이-남"][0]
    assert voices["엄마"] == VOICE_TYPES["어른-여"][0]
    assert voices["개구리"] == VOICE_TYPES["작은 동물"][0]


def test_same_type_twice_uses_spare_voice():
    # 주인공이 여자아이인데 친구도 여자아이면 예비 목소리를 쓴다
    voices = cast_voices({"친구": "아이-여", "언니": "아이-여"}, "girl", "narrator")
    girls = VOICE_TYPES["아이-여"]
    assert [voices[PROTAGONIST], voices["친구"], voices["언니"]] == list(girls[:3])


def test_unknown_type_gets_an_unused_voice():
    voices = cast_voices({"로봇": "기계"}, "girl", "narrator")
    assert voices["로봇"] not in {"narrator", voices[PROTAGONIST]}


def test_speaker_missing_from_cast_gets_a_voice():
    voices = cast_voices({"곰": "큰 동물"}, "girl", "narrator", ["나레이션", "개구리", "곰", "나"])
    assert voices["곰"] == VOICE_TYPES["큰 동물"][0]
    assert voices["개구리"] not in {"narrator", voices[PROTAGONIST], voices["곰"]}


def test_name_beats_wrong_type():
    # GPT 가 할머니를 어른-여로 적어도 할머니 목소리를 쓴다
    voices = cast_voices({"할머니": "어른-여", "우리 아빠": ""}, "girl", "narrator")
    assert voices["할머니"] == VOICE_TYPES["할머니"][0]
    assert voices["우리 아빠"] == VOICE_TYPES["어른-남"][0]


def test_tagged():
    assert tagged("안녕!", "기쁨") == "[happy] 안녕!"
    assert tagged("안녕!", "화남") == "안녕!"  # 목록에 없는 감정은 태그를 붙이지 않는다
    assert tagged("안녕!", "") == "안녕!"
