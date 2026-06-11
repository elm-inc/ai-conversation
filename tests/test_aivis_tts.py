"""AivisTTSService (apps/voice-agent/aivis_tts.py) の実エンジン不要な単体テスト (P3)。

AivisSpeech / ElevenLabs の HTTP 境界 (urllib.request.urlopen) をフェイクに差し替えて検証:
- run_tts が L0 正規化済みテキストで audio_query→synthesis を呼び、TTSAudioRawFrame
  (pipeline sample_rate へリサンプル・チャンク分割済) を yield する
- AivisSpeech のエラー/タイムアウトで ElevenLabs REST に自動フォールバックする (無音にしない)
- 二重障害・フォールバック未設定では ErrorFrame を返す (理由を観測可能にする)
- bot.py の TTS factory: TTS_ENGINE 既定で従来の ElevenLabsTTSService と完全一致、
  aivis で AivisTTSService (あい/ゆう に別 speaker)、構築失敗時は ElevenLabs に倒す

pipecat 未導入環境 (root venv 既定) ではモジュールごと skip する。実行は voice-agent の venv:
    uv run --project apps/voice-agent --with pytest,pytest-asyncio \
        pytest tests/test_aivis_tts.py
"""

from __future__ import annotations

import json
import math
import struct
import sys
import time
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path
from typing import Any
from wave import open as wave_open

import pytest

pytest.importorskip(
    "pipecat.transports.daily.transport",
    reason="pipecat[daily] 未導入 (apps/voice-agent の venv で実行する)",
)

_AGENT = Path(__file__).resolve().parents[1] / "apps" / "voice-agent"
if str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))

import bot  # noqa: E402
from aivis_tts import AivisTTSService, normalize  # noqa: E402
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame  # noqa: E402
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService  # noqa: E402

_AIVIS_URL = "http://127.0.0.1:10101"
_SPEAKER = 888753760  # まお ノーマル
_AIVIS_RATE = 44_100  # AivisSpeech 既定の出力レート


def _tone_pcm(rate: int, ms: int = 100) -> bytes:
    """検証用 220Hz 正弦波 (16-bit mono PCM)。"""
    n = rate * ms // 1000
    return b"".join(
        struct.pack("<h", int(20_000 * math.sin(2 * math.pi * 220 * i / rate)))
        for i in range(n)
    )


def _wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = BytesIO()
    with wave_open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _Resp:
    """urlopen の戻り (context manager + read) の最小フェイク。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class _FakeHTTP:
    """urllib.request.urlopen の置き換え。URL で AivisSpeech / ElevenLabs を演じ分ける。"""

    def __init__(
        self,
        *,
        aivis_fail: bool = False,
        aivis_delay_s: float = 0.0,
        eleven_fail: bool = False,
        tone_ms: int = 100,
    ) -> None:
        self.calls: list[tuple[str, bytes | None]] = []
        self.aivis_fail = aivis_fail
        self.aivis_delay_s = aivis_delay_s
        self.eleven_fail = eleven_fail
        self.tone_ms = tone_ms

    def __call__(self, req: Any, timeout: float | None = None) -> _Resp:
        url = req if isinstance(req, str) else req.full_url
        data = None if isinstance(req, str) else req.data
        self.calls.append((url, data))
        if "api.elevenlabs.io" in url:
            if self.eleven_fail:
                raise urllib.error.URLError("elevenlabs down (fake)")
            return _Resp(_tone_pcm(24_000, self.tone_ms))
        # 以降は AivisSpeech (VOICEVOX 互換)
        if self.aivis_delay_s:
            time.sleep(self.aivis_delay_s)
        if self.aivis_fail:
            raise urllib.error.URLError("aivis down (fake)")
        if "/audio_query" in url:
            return _Resp(
                json.dumps({"accent_phrases": [], "outputSamplingRate": _AIVIS_RATE}).encode()
            )
        if "/synthesis" in url:
            return _Resp(_wav_bytes(_tone_pcm(_AIVIS_RATE, self.tone_ms), _AIVIS_RATE))
        raise AssertionError(f"unexpected URL: {url}")


def _service(**kwargs: Any) -> AivisTTSService:
    defaults: dict[str, Any] = dict(
        base_url=_AIVIS_URL,
        speaker=_SPEAKER,
        fallback_api_key="fake-eleven-key",
        fallback_voice_id="fake-voice",
    )
    defaults.update(kwargs)
    return AivisTTSService(**defaults)


def _patch_http(monkeypatch: pytest.MonkeyPatch, fake: _FakeHTTP) -> None:
    # VoicevoxClient (aiconv) とフォールバック (aivis_tts) の両方が urllib.request.urlopen
    # を呼び出し時に解決するため、グローバルの 1 箇所を差し替えれば HTTP 境界を全て覆える。
    monkeypatch.setattr("urllib.request.urlopen", fake)


async def _collect(svc: AivisTTSService, text: str) -> list[Any]:
    return [f async for f in svc.run_tts(text, "ctx-test")]


# ---------------------------------------------------------------------------
# 正常系: L0 正規化 + 合成 + リサンプル + チャンク分割
# ---------------------------------------------------------------------------


async def test_run_tts_yields_audio_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP()
    _patch_http(monkeypatch, fake)
    svc = _service()

    text = "会議は10:30からです。"
    frames = await _collect(svc, text)

    assert frames, "音声フレームが yield されること"
    assert all(isinstance(f, TTSAudioRawFrame) for f in frames)
    assert all(f.num_channels == 1 and f.audio for f in frames)
    # StartFrame 前 (sample_rate 未確定) はエンジンのネイティブレートで素通し
    assert all(f.sample_rate == _AIVIS_RATE for f in frames)

    # audio_query へ L0 正規化済みテキストと speaker (style id) が渡ること
    query_url = next(url for url, _ in fake.calls if "/audio_query" in url)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(query_url).query)
    assert qs["text"][0] == normalize(text)
    assert qs["text"][0] != text, "L0 正規化 (10:30 → 読み下し表記) が効いていること"
    assert qs["speaker"][0] == str(_SPEAKER)
    assert any("/synthesis" in url for url, _ in fake.calls)
    assert not any("api.elevenlabs.io" in url for url, _ in fake.calls)


async def test_run_tts_resamples_and_chunks_to_pipeline_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHTTP(tone_ms=1200)  # 1.2s → 16kHz で 38400 bytes = 0.5s チャンク x3
    _patch_http(monkeypatch, fake)
    svc = _service()
    svc._sample_rate = 16_000  # StartFrame 相当 (pipeline の出力レート)

    frames = await _collect(svc, "こんにちは。")

    assert all(f.sample_rate == 16_000 for f in frames)
    total = sum(len(f.audio) for f in frames)
    assert total == pytest.approx(16_000 * 2 * 1.2, rel=0.01), "再生尺が維持されること"
    assert len(frames) == 3, "chunk_size (0.5s) で分割されること"
    assert all(len(f.audio) % 2 == 0 for f in frames), "16-bit 境界を跨がないこと"


async def test_empty_text_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP()
    _patch_http(monkeypatch, fake)
    assert await _collect(_service(), " ") == []
    assert fake.calls == [], "空テキストでエンジンを叩かないこと"


async def test_generator_close_midstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """割り込み相当: 消費を途中で打ち切っても例外なく閉じられる。"""
    _patch_http(monkeypatch, _FakeHTTP(tone_ms=1200))
    svc = _service()
    svc._sample_rate = 16_000
    gen = svc.run_tts("こんにちは。", "ctx-test")
    first = await anext(gen)
    assert isinstance(first, TTSAudioRawFrame)
    await gen.aclose()  # 残りフレームは破棄される


# ---------------------------------------------------------------------------
# フォールバック: AivisSpeech 障害時に ElevenLabs REST で同じ文を合成する
# ---------------------------------------------------------------------------


async def test_fallback_on_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP(aivis_fail=True)
    _patch_http(monkeypatch, fake)
    svc = _service()

    frames = await _collect(svc, "こんにちは。")

    assert frames and all(isinstance(f, TTSAudioRawFrame) for f in frames), "無音にしないこと"
    assert all(f.sample_rate == 24_000 for f in frames), "ElevenLabs REST は pcm_24000"
    eleven = [(url, data) for url, data in fake.calls if "api.elevenlabs.io" in url]
    assert eleven, "ElevenLabs にフォールバックすること"
    body = json.loads(eleven[0][1] or b"{}")
    assert body["text"] == normalize("こんにちは。"), "同じ正規化済みの文を合成すること"


async def test_fallback_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP(aivis_delay_s=0.5)
    _patch_http(monkeypatch, fake)
    svc = _service(timeout_s=0.05)

    frames = await _collect(svc, "こんにちは。")

    assert frames and all(isinstance(f, TTSAudioRawFrame) for f in frames)
    assert any("api.elevenlabs.io" in url for url, _ in fake.calls)


async def test_double_failure_yields_errorframe(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP(aivis_fail=True, eleven_fail=True)
    _patch_http(monkeypatch, fake)
    svc = _service()

    frames = await _collect(svc, "こんにちは。")

    assert len(frames) == 1 and isinstance(frames[0], ErrorFrame)
    assert not any(isinstance(f, TTSAudioRawFrame) for f in frames)


async def test_missing_fallback_credentials_yields_errorframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHTTP(aivis_fail=True)
    _patch_http(monkeypatch, fake)
    svc = _service(fallback_api_key=None, fallback_voice_id=None)

    frames = await _collect(svc, "こんにちは。")

    assert len(frames) == 1 and isinstance(frames[0], ErrorFrame)
    assert not any("api.elevenlabs.io" in url for url, _ in fake.calls)


# ---------------------------------------------------------------------------
# bot.py の TTS factory: env ゲート (既定で従来挙動と完全一致)
# ---------------------------------------------------------------------------


def test_tts_engine_defaults_to_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.delenv("TTS_ENGINE", raising=False)
    bot_mod = importlib.reload(bot)
    assert bot_mod.TTS_ENGINE == "elevenlabs"


def test_build_tts_default_is_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
    monkeypatch.setattr(bot, "TTS_ENGINE", "elevenlabs")
    tts = bot._build_tts(bot._default_spec())
    assert type(tts) is ElevenLabsTTSService, "既定では従来の ElevenLabs 構成のまま"


def test_build_tts_aivis_selected_with_per_agent_speaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "TTS_ENGINE", "aivis")
    spec = bot._default_spec()
    spec.aivis_speaker = 1878365376  # コハク ノーマル (ゆう相当)
    tts = bot._build_tts(spec)
    assert isinstance(tts, AivisTTSService)
    assert tts.speaker == 1878365376, "spec ごとの speaker (あい/ゆう) が渡ること"
    assert tts.base_url == bot.AIVIS_URL


def test_build_tts_aivis_construct_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
    monkeypatch.setattr(bot, "TTS_ENGINE", "aivis")
    # aivis_tts が import 不能なイメージ (aiconv 未同梱の cloud 等) を再現
    monkeypatch.setitem(sys.modules, "aivis_tts", None)
    tts = bot._build_tts(bot._default_spec())
    assert type(tts) is ElevenLabsTTSService, "構築失敗でも ElevenLabs に倒れる (無音にしない)"
