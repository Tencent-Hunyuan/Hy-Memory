"""Regression: extract the first JSON object even with trailing text."""

from hy_memory.agent.emotion_analyzer import EmotionAnalyzer


def test_json_with_trailing_prose_is_parsed():
    text = (
        'Here is the analysis: {"valence": 0.5, "arousal": 0.3, '
        '"dominant_emotion": "joy"} Hope that helps!'
    )
    assert EmotionAnalyzer._parse_json(text) == {
        "valence": 0.5,
        "arousal": 0.3,
        "dominant_emotion": "joy",
    }


def test_json_with_trailing_second_object_is_parsed():
    text = '{"valence": 0.5} trailing note {"other": 1}'
    assert EmotionAnalyzer._parse_json(text) == {"valence": 0.5}


def test_plain_json_and_code_fence_are_unchanged():
    assert EmotionAnalyzer._parse_json('{"valence": -0.2}') == {"valence": -0.2}
    assert EmotionAnalyzer._parse_json(
        '```json\n{"valence": 0.1, "arousal": 0.2}\n```'
    ) == {"valence": 0.1, "arousal": 0.2}


def test_invalid_text_still_returns_empty_dict():
    assert EmotionAnalyzer._parse_json("no json here") == {}
