"""TTSProvider 新アダプタ (ESPnet / VOICEVOX) の実エンジン不要な単体テスト (AIC-9 P2)。

エンジン呼び出し (EspnetSynthesizer / VoicevoxClient) をフェイクに差し替えて検証する:
- TTSProvider プロトコル適合 (runtime_checkable)
- L0 正規化の通過と _PCM_16K (16kHz mono) への正規化 (リサンプル)
- interrupt() による停止
- VoiceLicense ガード + AuditSink 記録 (許諾外は合成しない・記録は必ず残る)
- エンジン未導入/未起動でも import は成功し、synthesize が明確な例外 (導入・起動案内) を出す
- VOICEVOX への L1 アクセント注入 (inject_accent + mora_data 再計算)

実機合成 (GPU / VOICEVOX ENGINE) は環境準備後のフォローアップで行う。
"""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import AsyncIterator
from typing import Any

import pytest

from aiconv.adapters import tts_voicevox
from aiconv.adapters._engines import EngineUnavailableError, espnet_engine
from aiconv.adapters._engines.resample import resample_pcm16
from aiconv.adapters.tts_espnet import EspnetTTS
from aiconv.adapters.tts_voicevox import VoicevoxTTS, inject_accent
from aiconv.core.events import AudioFormat
from aiconv.core.ports import TTSProvider, VoiceLicense
from aiconv.frontend import AccentPhrase

_PCM_16K = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)


async def _chunks(*texts: str) -> AsyncIterator[str]:
    for t in texts:
        yield t


def _tone_pcm(rate: int, ms: int = 100) -> bytes:
    """検証用 220Hz 正弦波 (16-bit mono PCM)。"""
    n = rate * ms // 1000
    return b"".join(
        struct.pack("<h", int(20_000 * math.sin(2 * math.pi * 220 * i / rate)))
        for i in range(n)
    )


def _wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _Audit:
    def __init__(self) -> None:
        self.records: list[tuple[str, bool]] = []

    def record(self, *, voice_id: str, text: str, allowed: bool) -> None:
        self.records.append((text, allowed))


class _FakeEspnetEngine:
    """EspnetSynthesizer 互換のフェイク (22.05kHz 一括合成)。"""

    model_tag = "fake/model"
    device = "cpu"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str) -> tuple[bytes, int]:
        self.calls.append(text)
        return _tone_pcm(22_050), 22_050


def _vv_phrase(n_moras: int, accent: int = 1) -> dict[str, Any]:
    return {
        "moras": [{"text": "ア", "pitch": 5.0} for _ in range(n_moras)],
        "accent": accent,
        "is_interrogative": False,
    }


class _FakeVoicevoxClient:
    """VoicevoxClient 互換のフェイク (24kHz WAV を返す)。"""

    base_url = "http://fake:50021"
    speaker = 3

    def __init__(self) -> None:
        self.queries: list[str] = []

    def audio_query(self, text: str) -> dict[str, Any]:
        self.queries.append(text)
        return {"accent_phrases": [], "outputSamplingRate": 24_000}

    def mora_data(self, accent_phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise AssertionError("accent_phrases が一致しない限り mora_data は呼ばれない")

    def synthesis(self, query: dict[str, Any]) -> bytes:
        return _wav_bytes(_tone_pcm(24_000), 24_000)


# ---------------------------------------------------------------------------
# プロトコル適合 / 遅延 import (エンジン無しで import・生成できる)
# ---------------------------------------------------------------------------


def test_adapters_satisfy_ttsprovider_protocol() -> None:
    # 生成は espnet 未導入 / ENGINE 未起動でも成功する (遅延 import / lazy load 規約)
    assert isinstance(EspnetTTS(), TTSProvider)
    assert isinstance(VoicevoxTTS(), TTSProvider)


def test_espnet_capabilities_reflect_availability() -> None:
    from aiconv.adapters._engines.espnet_engine import espnet_available

    tts = EspnetTTS()
    assert tts.capabilities.available is (espnet_available() is None)
    assert tts.capabilities.supports_interrupt
    assert "ja" in tts.capabilities.languages


# ---------------------------------------------------------------------------
# ESPnet アダプタ
# ---------------------------------------------------------------------------


async def test_espnet_synthesize_normalizes_to_pcm16k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tts = EspnetTTS(voice_id="v")
    fake = _FakeEspnetEngine()
    monkeypatch.setattr(tts, "_engine", fake)
    frames = [f async for f in tts.synthesize(_chunks("こんにちは。", "3,000円です。"))]

    assert len(frames) == 2
    for i, frame in enumerate(frames):
        assert frame.fmt == _PCM_16K
        assert frame.seq == i
        # 100ms @22.05kHz (2205 サンプル) → 100ms @16kHz (1600 サンプル)
        assert len(frame.data) == 2 * 1_600
        assert abs(frame.duration_ms - 100.0) < 1.0
    # L0 正規化を通してからエンジンに渡す (桁区切りカンマの除去)
    assert fake.calls == ["こんにちは。", "3000円です。"]


async def test_espnet_interrupt_stops_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    tts = EspnetTTS()
    fake = _FakeEspnetEngine()
    monkeypatch.setattr(tts, "_engine", fake)
    agen = tts.synthesize(_chunks("一文目。", "二文目。", "三文目。"))
    first = await anext(agen)
    assert first.fmt == _PCM_16K
    await tts.interrupt()
    assert [f async for f in agen] == []
    assert fake.calls == ["一文目。"]  # 中断後は合成しない


async def test_espnet_guard_blocks_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = _Audit()
    tts = EspnetTTS(
        voice_id="voice-x",
        license=VoiceLicense(voice_id="voice-x", allow=lambda t: "禁止" not in t),
        audit=audit,
    )
    fake = _FakeEspnetEngine()
    monkeypatch.setattr(tts, "_engine", fake)
    frames = [f async for f in tts.synthesize(_chunks("こんにちは。", "禁止ワードです。"))]

    assert len(frames) == 1
    assert fake.calls == ["こんにちは。"]  # 許諾外は合成しない
    # 監査ログは許諾可否によらず必ず記録される
    assert audit.records == [("こんにちは。", True), ("禁止ワードです。", False)]


async def test_espnet_unavailable_raises_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """espnet 未導入でも import / 生成は成功し、synthesize が導入案内つきの例外を出す。"""
    monkeypatch.setattr(
        espnet_engine, "espnet_available", lambda: "torch/espnet2 未導入: uv sync --inexact ..."
    )
    tts = EspnetTTS()
    with pytest.raises(EngineUnavailableError, match="uv sync"):
        [f async for f in tts.synthesize(_chunks("テスト。"))]


# ---------------------------------------------------------------------------
# VOICEVOX アダプタ
# ---------------------------------------------------------------------------


async def test_voicevox_synthesize_normalizes_to_pcm16k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tts = VoicevoxTTS(voice_id="vv", use_accent=False)
    fake = _FakeVoicevoxClient()
    monkeypatch.setattr(tts, "_client", fake)
    frames = [f async for f in tts.synthesize(_chunks("14:30に会いましょう。"))]

    assert len(frames) == 1
    frame = frames[0]
    assert frame.fmt == _PCM_16K
    # 100ms @24kHz (2400 サンプル) → 100ms @16kHz (1600 サンプル)
    assert len(frame.data) == 2 * 1_600
    assert fake.queries == ["14時30分に会いましょう。"]  # L0 正規化 (時刻コロン) を通過


async def test_voicevox_interrupt_and_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = _Audit()
    tts = VoicevoxTTS(
        voice_id="vv",
        license=VoiceLicense(voice_id="vv", allow=lambda t: "禁止" not in t),
        audit=audit,
        use_accent=False,
    )
    fake = _FakeVoicevoxClient()
    monkeypatch.setattr(tts, "_client", fake)
    agen = tts.synthesize(_chunks("禁止ワード。", "一文目。", "二文目。"))
    first = await anext(agen)  # 許諾外をスキップして最初の許諾チャンクが出る
    assert first.seq == 0
    await tts.interrupt()
    assert [f async for f in agen] == []
    assert fake.queries == ["一文目。"]
    assert audit.records == [("禁止ワード。", False), ("一文目。", True)]


async def test_voicevox_engine_down_raises_launch_hint() -> None:
    """ENGINE 未起動 (接続不可) なら起動案内つきの明確な例外を出す。"""
    # 127.0.0.1:1 (tcpmux) は閉じているため即時に接続拒否される (外部ネットワーク不要)
    tts = VoicevoxTTS(base_url="http://127.0.0.1:1", use_accent=False)
    with pytest.raises(EngineUnavailableError, match="起動"):
        [f async for f in tts.synthesize(_chunks("テスト。"))]


# ---------------------------------------------------------------------------
# L1 アクセント注入 (VOICEVOX audio_query への反映)
# ---------------------------------------------------------------------------


def test_inject_accent_rewrites_nucleus_on_match() -> None:
    base = [_vv_phrase(3, accent=2), _vv_phrase(5, accent=1)]
    phrases = [
        AccentPhrase(reading="アメガ", accent=1, mora_count=3),
        AccentPhrase(reading="フルラシイ", accent=0, mora_count=5),  # 平板
    ]
    out = inject_accent(base, phrases)
    assert out is not None
    assert [p["accent"] for p in out] == [1, 5]  # 平板 (0) は句末核 = モーラ数へ変換
    assert [p["accent"] for p in base] == [2, 1]  # 元クエリは破壊しない (deep copy)


def test_inject_accent_conservative_on_mismatch() -> None:
    base = [_vv_phrase(3)]
    # モーラ数不一致 / 句数不一致では注入しない (None → ENGINE 既定アクセントのまま)
    assert inject_accent(base, [AccentPhrase("アメ", 1, 2)]) is None
    assert inject_accent(base, []) is None
    assert inject_accent([], [AccentPhrase("アメ", 1, 2)]) is None


async def test_voicevox_accent_injection_calls_mora_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """句構造が一致したら accent を書き換え、mora_data で再計算してから合成する。"""

    class Client(_FakeVoicevoxClient):
        def __init__(self) -> None:
            super().__init__()
            self.mora_calls = 0

        def audio_query(self, text: str) -> dict[str, Any]:
            self.queries.append(text)
            return {"accent_phrases": [_vv_phrase(3, accent=2)], "outputSamplingRate": 24_000}

        def mora_data(self, accent_phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
            self.mora_calls += 1
            assert [p["accent"] for p in accent_phrases] == [1]  # L1 の核位置が注入済み
            return accent_phrases

        def synthesis(self, query: dict[str, Any]) -> bytes:
            assert [p["accent"] for p in query["accent_phrases"]] == [1]
            return _wav_bytes(_tone_pcm(24_000), 24_000)

    # pyopenjtalk 未導入環境でも検証できるよう L1 をフェイク化 (アダプタ module 名前空間)
    monkeypatch.setattr(tts_voicevox, "frontend_available", lambda: None)
    monkeypatch.setattr(
        tts_voicevox,
        "predict_accent",
        lambda text: [AccentPhrase(reading="アメガ", accent=1, mora_count=3)],
    )
    tts = VoicevoxTTS()
    client = Client()
    monkeypatch.setattr(tts, "_client", client)
    frames = [f async for f in tts.synthesize(_chunks("雨が。"))]
    assert len(frames) == 1
    assert client.mora_calls == 1


async def test_voicevox_accent_injection_skipped_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """句構造が合わなければ既定クエリのまま合成する (mora_data は呼ばれない)。"""
    monkeypatch.setattr(tts_voicevox, "frontend_available", lambda: None)
    monkeypatch.setattr(
        tts_voicevox,
        "predict_accent",
        lambda text: [AccentPhrase(reading="ナガイヨミ", accent=1, mora_count=9)],
    )
    tts = VoicevoxTTS()
    fake = _FakeVoicevoxClient()  # accent_phrases=[] → 句数不一致 → 注入スキップ
    monkeypatch.setattr(tts, "_client", fake)
    frames = [f async for f in tts.synthesize(_chunks("テスト。"))]
    assert len(frames) == 1  # mora_data が呼ばれれば _FakeVoicevoxClient が AssertionError


# ---------------------------------------------------------------------------
# リサンプラ
# ---------------------------------------------------------------------------


def test_resample_pcm16_ratio_and_identity() -> None:
    pcm = _tone_pcm(22_050)  # 100ms
    assert resample_pcm16(pcm, 16_000, 16_000) == pcm  # 同レートは無変換
    assert resample_pcm16(b"", 22_050, 16_000) == b""
    assert len(resample_pcm16(pcm, 22_050, 16_000)) == 2 * 1_600  # down
    assert len(resample_pcm16(_tone_pcm(16_000), 16_000, 24_000)) == 2 * 2_400  # up


def test_resample_pcm16_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00\x00", 0, 16_000)
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00", 22_050, 16_000)  # 奇数バイト長
