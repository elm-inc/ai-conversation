from aiconv.adapters.turn_fusion import FusionTurnDetector
from aiconv.core.events import Transcript, TurnLabel


def _tr(text: str, *, endpoint_hint: bool = False, is_final: bool = False) -> Transcript:
    return Transcript(text, is_final=is_final, endpoint_hint=endpoint_hint)


def test_complete_sentence_without_audio() -> None:
    d = FusionTurnDetector()
    # 明確に完結したテキストは音響を待たず COMPLETE
    assert d.predict(_tr("今日はいい天気だね"), silence_ms=0).label is TurnLabel.COMPLETE


def test_joshidome_waits_without_audio_end() -> None:
    d = FusionTurnDetector()
    # 助詞止め + 無音短 + 音響ヒント無し → まだ待つ
    assert d.predict(_tr("今日はいい天気だけど"), silence_ms=100).label is TurnLabel.INCOMPLETE


def test_joshidome_yields_on_audio_end() -> None:
    d = FusionTurnDetector()
    # 助詞止めでも音響終端ヒントが来ればターン放棄として COMPLETE
    out = d.predict(_tr("今日はいい天気だけど", endpoint_hint=True), silence_ms=0)
    assert out.label is TurnLabel.COMPLETE


def test_filler_never_completes() -> None:
    d = FusionTurnDetector()
    # フィラーのみ → 音響終端が来てもまだ続くと判断
    out = d.predict(_tr("えーと", endpoint_hint=True), silence_ms=2000)
    assert out.label is TurnLabel.INCOMPLETE


def test_backchannel_label() -> None:
    d = FusionTurnDetector()
    assert d.predict(_tr("うん"), silence_ms=0).label is TurnLabel.BACKCHANNEL


def test_long_silence_completes_midlevel_text() -> None:
    d = FusionTurnDetector()
    # 体言止め等 (中程度スコア) でも長い無音なら COMPLETE
    out = d.predict(_tr("駅前のカフェ"), silence_ms=1000)
    assert out.label is TurnLabel.COMPLETE


def test_fragment_with_audio_end_still_waits() -> None:
    d = FusionTurnDetector()
    # 音響終端が来ても断片すぎる (助詞のみ等) なら待つ
    assert d.predict(_tr("を", endpoint_hint=True), silence_ms=0).label is TurnLabel.INCOMPLETE
