"""ESPnet2 VITS エンジン (Apache-2.0) — 本命候補。

G2P はモデル設定に焼き込まれている。既定モデル kan-bayashi/jsut_vits_accent_with_pause は
G2P=pyopenjtalk_accent_with_pause (アクセント明示入力) の JSUT 事前学習 VITS。
ESPNET_MODEL でモデルタグを差し替え可 (例: kan-bayashi/jsut_full_band_vits_prosody)。
話者適応 (あい/ゆう声) は P1 でこの事前学習からファインチューンする想定。

モデルは espnet_model_zoo 経由で初回ダウンロード。import 時にはロードせず、
prepare()/初回 synthesize まで遅延する (ハーネス規約)。
"""

from __future__ import annotations

import importlib.util
import os
import time
from typing import Any

from metrics import pcm_duration_s

from .base import Engine, EngineUnavailableError, SynthResult

_DEFAULT_MODEL = "kan-bayashi/jsut_vits_accent_with_pause"


class EspnetEngine(Engine):
    name = "espnet"

    def __init__(self, *, model_tag: str | None = None, device: str | None = None) -> None:
        self.model_tag = model_tag or os.environ.get("ESPNET_MODEL", _DEFAULT_MODEL)
        self.device = device or os.environ.get("ESPNET_DEVICE", "cpu")
        self._tts: Any = None  # espnet2.bin.tts_inference.Text2Speech (遅延ロード)

    def check(self) -> str | None:
        missing = [
            m
            for m in ("torch", "espnet2", "espnet_model_zoo", "pyopenjtalk")
            if importlib.util.find_spec(m) is None
        ]
        if missing:
            return (
                f"{'/'.join(missing)} 未導入: "
                "uv sync --inexact --extra tts-bench --extra tts-bench-espnet"
            )
        return None

    def prepare(self) -> None:
        if self._tts is not None:
            return
        reason = self.check()
        if reason is not None:
            raise EngineUnavailableError(f"{self.name}: {reason}")
        from espnet2.bin.tts_inference import Text2Speech

        # モデルタグは初回のみ DL される (espnet_model_zoo のキャッシュに保存)
        self._tts = Text2Speech.from_pretrained(model_tag=self.model_tag, device=self.device)

    def synthesize(self, text: str) -> SynthResult:
        self.prepare()
        import numpy as np

        t0 = time.perf_counter()
        out = self._tts(text)
        wav = out["wav"]  # torch.Tensor (float)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        arr = wav.detach().cpu().numpy()
        pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        sample_rate = int(self._tts.fs)
        return SynthResult(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=pcm_duration_s(pcm, sample_rate),
            ttfa_ms=elapsed_ms,  # Text2Speech は一括生成 (ストリーミング化は本採用後の課題)
            elapsed_ms=elapsed_ms,
            streaming=False,
            notes=f"model={self.model_tag} device={self.device}",
        )
