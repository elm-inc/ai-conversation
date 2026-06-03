from aiconv.core.events import (
    AudioFormat,
    AudioFrame,
    Transcript,
    TranscriptHealth,
    TurnDecision,
    TurnLabel,
)


def test_audio_frame_duration() -> None:
    fmt = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)
    frame = AudioFrame(data=b"\x00\x00" * 160, ts_ms=0.0, fmt=fmt)  # 160 samples @16k = 10ms
    assert abs(frame.duration_ms - 10.0) < 1e-6


def test_transcript_usable_gate() -> None:
    assert Transcript("こんにちは", is_final=True).is_usable
    assert not Transcript("  ", is_final=True).is_usable
    assert not Transcript("ノイズ", is_final=True, health=TranscriptHealth.ABNORMAL).is_usable


def test_turn_decision_complete() -> None:
    assert TurnDecision(TurnLabel.COMPLETE).is_complete
    assert not TurnDecision(TurnLabel.INCOMPLETE).is_complete
