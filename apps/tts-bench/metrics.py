"""計測ユーティリティ (TTFA / RTF / VRAM / 音声長)。

- TTFA(ms): 合成要求〜最初の音声バイト (エンジン側で計測し SynthResult に載せる)
- RTF: 合成所要時間 / 生成音声長 (<1 で実時間より速い)
- VRAM: torch.cuda が使える環境でのみ peak allocated を測る。無ければ N/A (None)。
  torch.cuda.max_memory_allocated は同一プロセスの torch 確保分のみ追跡するため、
  別プロセスの VOICEVOX ENGINE 等は対象外 (レポートに注記)。
"""

from __future__ import annotations

import importlib.util
from typing import Any


def pcm_duration_s(
    pcm: bytes, sample_rate: int, *, sample_width: int = 2, channels: int = 1
) -> float:
    """16-bit PCM バイト列の音声長 (秒)。"""
    if sample_rate <= 0:
        return 0.0
    return len(pcm) / (sample_width * channels) / sample_rate


def rtf(elapsed_ms: float, duration_s: float) -> float | None:
    """Real-Time Factor。音声長 0 なら None。"""
    if duration_s <= 0:
        return None
    return (elapsed_ms / 1000.0) / duration_s


def _torch_cuda() -> Any | None:
    """torch が導入済みかつ CUDA が使えるときだけ torch モジュールを返す (遅延 import)。"""
    if importlib.util.find_spec("torch") is None:
        return None
    import torch

    return torch if torch.cuda.is_available() else None


def vram_reset() -> None:
    """VRAM ピーク計測をリセットする (CUDA が無ければ何もしない)。"""
    t = _torch_cuda()
    if t is not None:
        t.cuda.reset_peak_memory_stats()


def vram_peak_mb() -> float | None:
    """直近 reset 以降の torch CUDA peak allocated (MB)。CUDA が無ければ None。"""
    t = _torch_cuda()
    if t is None:
        return None
    return float(t.cuda.max_memory_allocated()) / 1e6
