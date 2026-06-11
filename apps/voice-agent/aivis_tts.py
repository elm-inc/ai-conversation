"""AivisSpeech 用 Pipecat TTSService (P3: 本番ボットのセルフホスト TTS 経路)。

AivisSpeech ENGINE (Style-Bert-VITS2/ONNX) は VOICEVOX 互換の外部 HTTP エンジン
(既定 http://127.0.0.1:10101、audio_query → synthesis)。クライアントは aiconv の
VoicevoxClient をそのまま再利用する — LGPL エンジンを HTTP プロセス分離で利用するため
本リポのコードにライセンスは伝播せず、pip 依存追加もない (設計 japanese-tts-optimization §3)。

- 合成前に L0 正規化 (aiconv.frontend.normalize) を通す (記号/数字/英単語の読み崩れ対策)
- 出力 WAV (AivisSpeech 既定 44.1kHz) を pipeline の sample_rate へ SOXR リサンプルし、
  TTSAudioRawFrame で yield する (TTSStartedFrame/TTSStoppedFrame は pipecat の HTTP 型
  TTS 規約 push_start_frame/push_stop_frames=True により基底クラスが管理)
- AivisSpeech がエラー/タイムアウトのときは ElevenLabs REST (pcm_24000) へ自動フォール
  バックする。発火・TTFA・合成失敗は loguru で観測できる (設計 §13)。二重障害のときのみ
  ErrorFrame (無音をログ無しで起こさない)
- 文単位の非ストリーミング合成: pipecat の SENTENCE aggregation が文ごとに run_tts を
  呼ぶため割り込みは文単位で効く (interruption 時は audio context ごと破棄される)。
  HTTP 待ちは asyncio.timeout (AIVIS_TIMEOUT_S) で打ち切り、長文でも無音で固まらない

aiconv は repo 内 (src/) を sys.path 経由で import する (processors.py の staging 注記と
同じ扱い)。Pipecat Cloud イメージで使うには aiconv の同梱が別途必要 — 無いイメージでは
本モジュールの import が失敗し、bot.py の factory が ElevenLabs に倒す。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from collections.abc import AsyncGenerator
from pathlib import Path

from loguru import logger
from pipecat.audio.utils import create_file_resampler
from pipecat.frames.frames import ErrorFrame, Frame, StartFrame, TTSAudioRawFrame
from pipecat.services.tts_service import TTSService

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    # voice-agent は独立 uv プロジェクト (aiconv 非依存) のため、repo 内実行では src/ を足す
    sys.path.insert(0, str(_REPO_SRC))

from aiconv.adapters._engines import EngineUnavailableError  # noqa: E402
from aiconv.adapters._engines.voicevox_engine import VoicevoxClient, parse_wav  # noqa: E402
from aiconv.frontend import normalize  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:10101"
DEFAULT_SPEAKER = 888753760  # まお ノーマル (起動中の ENGINE の /speakers で確認・変更可)
DEFAULT_TIMEOUT_S = 10.0  # audio_query + synthesis の合計予算 (超過でフォールバック)
ENV_URL = "AIVIS_URL"
ENV_SPEAKER = "AIVIS_SPEAKER"
ENV_TIMEOUT = "AIVIS_TIMEOUT_S"
_FALLBACK_RATE = 24_000  # ElevenLabs REST の出力フォーマット (pcm_24000)
_FALLBACK_CHUNK = 9_600  # sample_rate 未確定時 (StartFrame 前) のフレーム分割幅


class AivisTTSService(TTSService):
    """AivisSpeech (VOICEVOX 互換 HTTP) の TTSService。ElevenLabs REST フォールバック内蔵。

    run_tts は TTSAudioRawFrame だけを yield する (Started/Stopped/audio context は基底が
    管理)。フォールバックは bot.py の _duo_tts と同じ ElevenLabs REST 経路で、Websocket 版
    ElevenLabsTTSService の lifecycle を二重に持たずに「このターンを無音にしない」を満たす。
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        speaker: int | None = None,
        timeout_s: float | None = None,
        fallback_api_key: str | None = None,
        fallback_voice_id: str | None = None,
        fallback_model: str = "eleven_multilingual_v2",
        sample_rate: int | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            push_start_frame=True,
            push_stop_frames=True,
            **kwargs,
        )
        timeout = (
            timeout_s
            if timeout_s is not None
            else float(os.getenv(ENV_TIMEOUT) or DEFAULT_TIMEOUT_S)
        )
        # VoicevoxClient の env 既定 (VOICEVOX_URL=:50021) を継がないよう必ず明示して渡す
        self._client = VoicevoxClient(
            base_url=base_url or os.getenv(ENV_URL) or DEFAULT_URL,
            speaker=(
                speaker if speaker is not None else int(os.getenv(ENV_SPEAKER) or DEFAULT_SPEAKER)
            ),
            synthesis_timeout_s=timeout,
        )
        self._timeout_s = timeout
        self._fb_api_key = fallback_api_key or ""
        self._fb_voice_id = fallback_voice_id or ""
        self._fb_model = fallback_model
        self._check_task: asyncio.Task[None] | None = None
        # one-shot (utterance 完結) リサンプラ。基底の _resampler (stream 版) は同一レート
        # ペア前提のため使わない — aivis(44.1k) と fallback(24k) でレートが混在する。
        self._utt_resampler = create_file_resampler()

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def speaker(self) -> int:
        return self._client.speaker

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        # 観測性: ENGINE 疎通を背景で確認 (起動は block しない。不可でもフォールバックが効く)
        self._check_task = asyncio.create_task(self._log_engine_check())

    async def _log_engine_check(self) -> None:
        reason = await asyncio.to_thread(self._client.check)
        if reason is None:
            logger.info("[aivis] ENGINE 疎通 OK ({}, speaker={})", self.base_url, self.speaker)
        else:
            logger.warning("[aivis] {} — 合成は ElevenLabs にフォールバックされる", reason)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """text を L0 正規化 → AivisSpeech 合成 → TTSAudioRawFrame で yield。失敗時は FB。"""
        t0 = time.monotonic()
        norm = normalize(text)  # L0 (読み崩れ対策)。コンテキストには元テキストが残る
        if not norm.strip():
            return
        logger.debug("[aivis] Generating TTS [{}]", norm)
        try:
            async with asyncio.timeout(self._timeout_s):
                # ブロッキング HTTP はスレッドへ逃がし、イベントループ (音声配信) を止めない
                query = await asyncio.to_thread(self._client.audio_query, norm)
                wav = await asyncio.to_thread(self._client.synthesis, query)
            pcm, rate = parse_wav(wav)
        except asyncio.CancelledError:
            raise  # 割り込み (interruption) はそのまま上へ
        except (EngineUnavailableError, RuntimeError, TimeoutError, OSError, ValueError) as e:
            logger.warning(
                "[aivis] 合成失敗 → ElevenLabs フォールバック ({}: {})", type(e).__name__, e
            )
            async for frame in self._fallback_tts(norm, context_id, t0):
                yield frame
            return
        await self.start_tts_usage_metrics(norm)
        async for frame in self._yield_pcm(pcm, rate, context_id, t0, engine="aivis"):
            yield frame

    async def _fallback_tts(
        self, text: str, context_id: str, t0: float
    ) -> AsyncGenerator[Frame, None]:
        """ElevenLabs REST (bot.py の _duo_tts と同じ経路) で同じ文を合成する。"""
        if not (self._fb_api_key and self._fb_voice_id):
            logger.error("[aivis] フォールバック不可 (ElevenLabs api_key/voice_id 未設定)")
            yield ErrorFrame(error=f"aivis: 合成失敗かつフォールバック未設定 [{text[:40]}]")
            return
        try:
            pcm = await asyncio.to_thread(self._elevenlabs_rest, text)
        except Exception as e:  # noqa: BLE001 — 二重障害。無音の理由をログで観測可能にする
            logger.error("[aivis] フォールバック ElevenLabs も失敗 ({}: {})", type(e).__name__, e)
            yield ErrorFrame(error=f"aivis: ElevenLabs フォールバック失敗: {e}")
            return
        await self.start_tts_usage_metrics(text)
        async for frame in self._yield_pcm(
            pcm, _FALLBACK_RATE, context_id, t0, engine="fallback:elevenlabs"
        ):
            yield frame

    def _elevenlabs_rest(self, text: str) -> bytes:
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self._fb_voice_id}"
            "?output_format=pcm_24000"
        )
        body = json.dumps({"text": text, "model_id": self._fb_model}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"xi-api-key": self._fb_api_key, "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data: bytes = r.read()
        return data

    async def _yield_pcm(
        self, pcm: bytes, src_rate: int, context_id: str, t0: float, *, engine: str
    ) -> AsyncGenerator[Frame, None]:
        """PCM を pipeline の sample_rate へリサンプルし、チャンク分割して yield する。"""
        out_rate = self.sample_rate or src_rate  # StartFrame 前 (単体テスト直叩き) は素通し
        if src_rate != out_rate:
            pcm = await self._utt_resampler.resample(pcm, src_rate, out_rate)
        if not pcm:
            return
        await self.stop_ttfb_metrics()
        logger.info(
            "[aivis] TTFA {:.0f}ms engine={} ({:.2f}s audio @ {}Hz)",
            (time.monotonic() - t0) * 1000,
            engine,
            len(pcm) / 2 / out_rate,
            out_rate,
        )
        step = self.chunk_size or _FALLBACK_CHUNK
        for i in range(0, len(pcm), step):
            yield TTSAudioRawFrame(pcm[i : i + step], out_rate, 1, context_id=context_id)
