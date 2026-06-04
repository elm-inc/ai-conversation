from aiconv.core.endpointing import (
    ends_with_filler,
    is_backchannel,
    is_filler_only,
    looks_like_interruption,
    text_completion_score,
)


def test_score_terminal_and_final_particle() -> None:
    assert text_completion_score("今日はいい天気です。") >= 0.9
    assert text_completion_score("今日はいい天気だね") >= 0.8  # 文末助詞
    assert text_completion_score("もう行きます") >= 0.8  # 述語語尾


def test_score_joshidome_is_low() -> None:
    # 助詞止め (継続) は完結度 低〜中
    assert text_completion_score("今日はいい天気だけど") < 0.5
    assert text_completion_score("それで") < 0.5


def test_score_filler_is_lowest() -> None:
    assert text_completion_score("えーと") <= 0.2
    assert text_completion_score("今日はね、えーと") <= 0.2


def test_filler_helpers() -> None:
    assert is_filler_only("あの")
    assert not is_filler_only("あのね")
    assert ends_with_filler("うーんと言いつつ、えっと")


def test_backchannel() -> None:
    assert is_backchannel("うん")
    assert is_backchannel("なるほど")
    assert not is_backchannel("うん、そうだね")  # 続きがある


def test_interruption_marker() -> None:
    assert looks_like_interruption("いや、ちがう")
    assert looks_like_interruption("ちょっとまって")
    assert not looks_like_interruption("そうですね")
