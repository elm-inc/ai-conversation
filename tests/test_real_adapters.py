"""実プロバイダアダプタの key 不要な単体テスト。

ネットワーク/API キー無しで検証できる範囲: 健全性判定・許諾ガード/監査・WAV 往復。
SDK は遅延 import なので、これらのテストは extras 未導入でも動く。
"""

from __future__ import annotations

import wave
from pathlib import Path

from aiconv.adapters.stt_deepgram import classify_health
from aiconv.adapters.transport_wav import WavFileTransport
from aiconv.adapters.tts_elevenlabs import ElevenLabsTTS
from aiconv.core.events import TranscriptHealth
from aiconv.core.ports import VoiceLicense


def test_classify_health() -> None:
    assert classify_health("こんにちは", 0.9) is TranscriptHealth.OK
    assert classify_health("", 0.9) is TranscriptHealth.ABNORMAL
    assert classify_health("ああああああ", 0.9) is TranscriptHealth.ABNORMAL  # ノイズ反復
    assert classify_health("はい", 0.2) is TranscriptHealth.LOW_CONFIDENCE


def test_tts_guard_records_audit_and_blocks() -> None:
    records: list[tuple[str, bool]] = []

    class Audit:
        def record(self, *, voice_id: str, text: str, allowed: bool) -> None:
            records.append((text, allowed))

    tts = ElevenLabsTTS(
        voice_id="voice-x",
        license=VoiceLicense(voice_id="voice-x", allow=lambda t: "禁止" not in t),
        audit=Audit(),
    )
    assert tts._guard("こんにちは") is True
    assert tts._guard("禁止ワード") is False
    # 監査ログは許諾可否によらず必ず記録される
    assert records == [("こんにちは", True), ("禁止ワード", False)]


async def test_wav_transport_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "in.wav"
    dst = tmp_path / "out.wav"
    # 100ms の無音 16k mono 16-bit
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 1_600)

    transport = WavFileTransport(str(src), str(dst), frame_ms=20, realtime=False)
    total = 0
    async for frame in transport.inbound():
        total += len(frame.data)
        await transport.play(frame)
    await transport.stop_playback()

    assert total == 2 * 1_600  # 全サンプルを読み出した
    assert dst.exists()
    with wave.open(str(dst), "rb") as w:
        assert w.getframerate() == 16_000
        assert w.getnframes() == 1_600
