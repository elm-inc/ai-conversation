"""ESPnet2 VITS (Apache-2.0) の低レベル合成 — 設計 japanese-tts-optimization §3 の本命。

`Text2Speech` の lazy ロードと一括合成 (text → 16-bit mono PCM) のみを担う同期層。
async 化・AudioFrame 正規化・L0/L1 は core アダプタ (tts_espnet)、TTFA/RTF 計測は
apps/tts-bench/engines/espnet.py の責務 (両者がこのモジュールを共有する)。

既定モデル kan-bayashi/jsut_vits_accent_with_pause は G2P=pyopenjtalk_accent_with_pause
(アクセント明示入力) の JSUT 事前学習 VITS。ESPNET_MODEL でタグ差し替え可
(例: kan-bayashi/jsut_full_band_vits_prosody)。話者適応 (あい/ゆう声) はこの事前学習から
ファインチューンする想定 (設計 §5-L5)。
モデルは espnet_model_zoo 経由で初回ダウンロード。import / 生成時にはロードせず、
load() (または初回 synthesize) まで遅延する。
"""

from __future__ import annotations

import importlib.util
import os
import threading
from typing import Any

from . import EngineUnavailableError

DEFAULT_MODEL = "kan-bayashi/jsut_vits_accent_with_pause"
ENV_MODEL = "ESPNET_MODEL"
ENV_DEVICE = "ESPNET_DEVICE"
INSTALL_HINT = "uv sync --inexact --extra tts-bench --extra tts-bench-espnet"

_REQUIRED = ("torch", "espnet2", "espnet_model_zoo", "pyopenjtalk")


def espnet_available() -> str | None:
    """espnet スタックが import 可能か。None = 可、str = 不可の理由 (導入案内)。"""
    missing = [m for m in _REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        return f"{'/'.join(missing)} 未導入: {INSTALL_HINT}"
    return None


class EspnetSynthesizer:
    """Text2Speech の lazy ロード + 一括合成 (非ストリーミング)。"""

    def __init__(self, *, model_tag: str | None = None, device: str | None = None) -> None:
        self.model_tag = model_tag or os.environ.get(ENV_MODEL, DEFAULT_MODEL)
        self.device = device or os.environ.get(ENV_DEVICE, "cpu")
        self._tts: Any = None  # espnet2.bin.tts_inference.Text2Speech (遅延ロード)
        self._load_lock = threading.Lock()  # asyncio.to_thread 経由の並行ロードを直列化

    @property
    def loaded(self) -> bool:
        return self._tts is not None

    def load(self) -> None:
        """モデルをロードする (初回のみ DL)。利用不可なら EngineUnavailableError。"""
        with self._load_lock:
            if self._tts is not None:
                return
            reason = espnet_available()
            if reason is not None:
                raise EngineUnavailableError(f"espnet: {reason}")
            from espnet2.bin.tts_inference import Text2Speech

            self._tts = Text2Speech.from_pretrained(model_tag=self.model_tag, device=self.device)

    def synthesize(self, text: str) -> tuple[bytes, int]:
        """text を一括合成して (16-bit mono PCM, ネイティブ sample_rate) を返す。"""
        self.load()
        import numpy as np

        out = self._tts(text)
        arr = out["wav"].detach().cpu().numpy()  # torch.Tensor (float) → numpy
        data: bytes = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return data, int(self._tts.fs)
