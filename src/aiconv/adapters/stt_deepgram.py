"""Deepgram STTProvider アダプタ — スケルトン。

★アダプタ責務 (設計レビュー反映): 健全性ゲート。確信度・異常長・繰り返しを検出し、
  Transcript.health に正規化してコアへ渡す (異常出力で投機応答が暴走するのを防ぐ)。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from ..core.events import AudioFrame, Transcript, TranscriptHealth
from ..core.ports import Capability


def classify_health(text: str, confidence: float) -> TranscriptHealth:
    """ベンダー非依存の健全性判定 (アダプタ共通で使える素朴版)。"""
    stripped = text.strip()
    if not stripped:
        return TranscriptHealth.ABNORMAL
    # 単一文字の異常反復 (ノイズ誤認) を異常とみなす
    if len(stripped) >= 6 and len(set(stripped)) == 1:
        return TranscriptHealth.ABNORMAL
    if confidence < 0.4:
        return TranscriptHealth.LOW_CONFIDENCE
    return TranscriptHealth.OK


class DeepgramSTT:
    capabilities = Capability(
        streaming_partials=True, languages=("ja",), notes="Deepgram streaming"
    )

    def __init__(self, *, language: str = "ja", api_key: str | None = None) -> None:
        self.language = language
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")

    async def transcribe(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Transcript]:
        raise NotImplementedError(
            "Phase 0 スケルトン: Deepgram streaming を実装し classify_health() で正規化 (AIC-1)"
        )
        yield
