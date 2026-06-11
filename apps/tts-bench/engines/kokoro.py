"""Kokoro-82M エンジン (Apache-2.0、重みも) — 即時汎用ボイス候補。

kokoro.KPipeline(lang_code="j") + misaki[ja] G2P。重み (hexgrad/Kokoro-82M) は初回に
Hugging Face から DL。日本語ボイスは jf_alpha / jf_gongitsune / jf_nezumi / jf_tebukuro /
jm_kumo (KOKORO_VOICE で変更)。

注意: misaki[ja] は本家 pyopenjtalk に依存し pyopenjtalk-plus と同居できない
(pyproject [tool.uv].conflicts 参照)。出力は 24kHz mono。
"""

from __future__ import annotations

import importlib.util
import os
import time
from typing import Any

from metrics import pcm_duration_s

from .base import Engine, EngineUnavailableError, SynthResult

_DEFAULT_VOICE = "jf_alpha"
_SAMPLE_RATE = 24_000  # Kokoro-82M 固定


class KokoroEngine(Engine):
    name = "kokoro"

    def __init__(self, *, voice: str | None = None, device: str | None = None) -> None:
        self.voice = voice or os.environ.get("KOKORO_VOICE", _DEFAULT_VOICE)
        self.device = device or os.environ.get("KOKORO_DEVICE") or None
        self._pipeline: Any = None  # kokoro.KPipeline (遅延ロード)

    def check(self) -> str | None:
        missing = [
            m
            for m in ("torch", "kokoro", "misaki", "pyopenjtalk")
            if importlib.util.find_spec(m) is None
        ]
        if missing:
            return (
                f"{'/'.join(missing)} 未導入: uv sync --inexact --extra tts-bench-kokoro "
                "(misaki[ja] の日本語 G2P 込み。pyopenjtalk-plus とは排他 — README 参照)"
            )
        return None

    def prepare(self) -> None:
        if self._pipeline is not None:
            return
        reason = self.check()
        if reason is not None:
            raise EngineUnavailableError(f"{self.name}: {reason}")
        from kokoro import KPipeline

        # 初回は HF から重み DL。device=None で自動選択 (cuda があれば cuda)
        self._pipeline = KPipeline(lang_code="j", device=self.device)

    def synthesize(self, text: str) -> SynthResult:
        self.prepare()
        import numpy as np

        t0 = time.perf_counter()
        ttfa_ms: float | None = None
        parts: list[Any] = []
        # KPipeline はテキストをチャンク分割して逐次生成する generator
        for result in self._pipeline(text, voice=self.voice):
            audio = getattr(result, "audio", None)
            if audio is None:
                continue
            if ttfa_ms is None:
                ttfa_ms = (time.perf_counter() - t0) * 1000.0
            parts.append(audio.detach().cpu().numpy())
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if not parts:
            raise RuntimeError("kokoro が音声を返さなかった")
        arr = np.concatenate(parts)
        pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return SynthResult(
            pcm=pcm,
            sample_rate=_SAMPLE_RATE,
            duration_s=pcm_duration_s(pcm, _SAMPLE_RATE),
            ttfa_ms=ttfa_ms if ttfa_ms is not None else elapsed_ms,
            elapsed_ms=elapsed_ms,
            streaming=True,  # チャンク単位の逐次生成
            notes=f"voice={self.voice}",
        )
