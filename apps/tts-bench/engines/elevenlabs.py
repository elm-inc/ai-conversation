"""ElevenLabs エンジン (比較基準 = 現行採用)。

src/aiconv/adapters/tts_elevenlabs.py (ストリーミング, pcm_16000 正規化) をそのまま使い、
最初の AudioFrame 到着までを TTFA として計測する。API キーは ~/.elevenlabs_token
(conversation-tester の _tok 規約) または ELEVENLABS_API_KEY。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from collections.abc import AsyncIterator

from metrics import pcm_duration_s

from .base import Engine, EngineUnavailableError, SynthResult, read_token

# conversation-tester presets.py の AI_VOICE_ID (あい本番ボイス) と揃える。
# 別 app パッケージのため import せず値を複製 (変更時は両方更新)。
_DEFAULT_VOICE_ID = "lhTvHflPVOqgSWyuWQry"


class ElevenLabsEngine(Engine):
    name = "elevenlabs"

    def __init__(self, *, voice_id: str | None = None, model_id: str | None = None) -> None:
        self.voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", _DEFAULT_VOICE_ID)
        self.model_id = model_id or os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")

    def _api_key(self) -> str:
        return os.environ.get("ELEVENLABS_API_KEY") or read_token("elevenlabs")

    def check(self) -> str | None:
        if importlib.util.find_spec("elevenlabs") is None:
            return "elevenlabs SDK 未導入: uv sync --inexact --extra providers"
        if not self._api_key():
            return "~/.elevenlabs_token も ELEVENLABS_API_KEY も無い"
        return None

    def synthesize(self, text: str) -> SynthResult:
        reason = self.check()
        if reason is not None:
            raise EngineUnavailableError(f"{self.name}: {reason}")
        return asyncio.run(self._synth(text))

    async def _synth(self, text: str) -> SynthResult:
        from aiconv.adapters.tts_elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(
            voice_id=self.voice_id, model_id=self.model_id, api_key=self._api_key()
        )

        async def one() -> AsyncIterator[str]:
            yield text

        t0 = time.perf_counter()
        ttfa_ms: float | None = None
        buf = bytearray()
        sample_rate = 16_000
        async for frame in tts.synthesize(one()):
            if ttfa_ms is None:
                ttfa_ms = (time.perf_counter() - t0) * 1000.0
            buf += frame.data
            sample_rate = frame.fmt.sample_rate
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if not buf:
            raise RuntimeError("ElevenLabs が音声を返さなかった")
        pcm = bytes(buf)
        return SynthResult(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=pcm_duration_s(pcm, sample_rate),
            ttfa_ms=ttfa_ms if ttfa_ms is not None else elapsed_ms,
            elapsed_ms=elapsed_ms,
            streaming=True,
            notes=f"voice={self.voice_id} model={self.model_id}",
        )
