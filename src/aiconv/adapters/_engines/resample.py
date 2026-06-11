"""16-bit mono PCM のリサンプリング (線形補間)。

TTS エンジンのネイティブ出力 (ESPnet VITS=22.05kHz, VOICEVOX 既定=24kHz) を core 規約の
16kHz (`AudioFormat()` 既定, 設計 japanese-tts-optimization §5-L4/L6) へ正規化するための
最小実装。numpy が導入済みなら高速パス、無ければ純 Python (base install は依存ゼロのため)。

注: ローパスフィルタは省略している (ダウンサンプリング時に 8kHz 超が折り返す)。音声帯域では
実用上目立たないが、高品質化 (soxr/scipy 導入) は実機評価後の課題として残す。
"""

from __future__ import annotations

import array
import importlib.util
import sys


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """16-bit mono リトルエンディアン PCM を線形補間でリサンプルする。"""
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError(f"不正なサンプルレート: {src_rate} -> {dst_rate}")
    if len(pcm) % 2:
        raise ValueError("16-bit PCM はバイト長が偶数のはず")
    if src_rate == dst_rate or not pcm:
        return pcm
    if importlib.util.find_spec("numpy") is not None:
        return _resample_numpy(pcm, src_rate, dst_rate)
    return _resample_pure(pcm, src_rate, dst_rate)


def _resample_numpy(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    import numpy as np

    src = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    n_src = int(src.shape[0])
    n_dst = max(1, round(n_src * dst_rate / src_rate))
    pos = np.arange(n_dst, dtype=np.float64) * (src_rate / dst_rate)
    j = np.minimum(pos.astype(np.int64), n_src - 1)
    j1 = np.minimum(j + 1, n_src - 1)
    frac = pos - j
    out = src[j] * (1.0 - frac) + src[j1] * frac
    data: bytes = np.clip(np.round(out), -32768, 32767).astype("<i2").tobytes()
    return data


def _resample_pure(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    src = array.array("h")
    src.frombytes(pcm)
    if sys.byteorder == "big":
        src.byteswap()
    n_src = len(src)
    n_dst = max(1, round(n_src * dst_rate / src_rate))
    ratio = src_rate / dst_rate
    dst = array.array("h", bytes(2 * n_dst))
    last = n_src - 1
    for i in range(n_dst):
        pos = i * ratio
        j = int(pos)
        if j >= last:
            dst[i] = src[last]
        else:
            frac = pos - j
            dst[i] = int(src[j] + (src[j + 1] - src[j]) * frac)
    if sys.byteorder == "big":
        dst.byteswap()
    return dst.tobytes()
