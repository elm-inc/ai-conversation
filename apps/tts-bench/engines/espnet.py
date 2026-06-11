"""ESPnet2 VITS エンジン (Apache-2.0) — 本命候補。

低レベル合成 (Text2Speech の lazy ロード + 一括合成) は aiconv.adapters._engines.espnet_engine
に委譲する (core アダプタ tts_espnet と共有 — 二重実装の禁止)。本モジュールはベンチ計測
(TTFA/elapsed/SynthResult) のみを担う。モデルタグは ESPNET_MODEL、デバイスは ESPNET_DEVICE
で差し替え可 (詳細は espnet_engine のモジュール docstring 参照)。
"""

from __future__ import annotations

import time

from metrics import pcm_duration_s

from aiconv.adapters._engines import EngineUnavailableError as CoreEngineUnavailableError
from aiconv.adapters._engines.espnet_engine import EspnetSynthesizer, espnet_available

from .base import Engine, EngineUnavailableError, SynthResult


class EspnetEngine(Engine):
    name = "espnet"

    def __init__(self, *, model_tag: str | None = None, device: str | None = None) -> None:
        self._core = EspnetSynthesizer(model_tag=model_tag, device=device)

    @property
    def model_tag(self) -> str:
        return self._core.model_tag

    @property
    def device(self) -> str:
        return self._core.device

    def check(self) -> str | None:
        return espnet_available()

    def prepare(self) -> None:
        try:
            self._core.load()  # モデルタグは初回のみ DL される (espnet_model_zoo のキャッシュ)
        except CoreEngineUnavailableError as e:
            raise EngineUnavailableError(str(e)) from e

    def synthesize(self, text: str) -> SynthResult:
        self.prepare()
        t0 = time.perf_counter()
        try:
            pcm, sample_rate = self._core.synthesize(text)
        except CoreEngineUnavailableError as e:
            raise EngineUnavailableError(str(e)) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return SynthResult(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=pcm_duration_s(pcm, sample_rate),
            ttfa_ms=elapsed_ms,  # Text2Speech は一括生成 (ストリーミング化は本採用後の課題)
            elapsed_ms=elapsed_ms,
            streaming=False,
            notes=f"model={self.model_tag} device={self.device}",
        )
