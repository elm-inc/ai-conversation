"""Deepgram STTProvider アダプタ (deepgram-sdk v7, live websocket)。

frames を socket に送りつつ Results メッセージを受信し、Transcript に正規化する。

★アダプタ責務 (設計レビュー反映): 健全性ゲート。確信度・異常反復を classify_health() で
  判定し Transcript.health に載せてコアへ渡す (異常出力で投機応答が暴走するのを防ぐ)。

SDK は遅延 import。日本語 streaming を前提に encoding=linear16 / sample_rate=16000。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

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
        streaming_partials=True, languages=("ja",), notes="Deepgram v7 live; 健全性ゲート付き"
    )

    def __init__(
        self,
        *,
        model: str = "nova-2",
        language: str = "ja",
        sample_rate: int = 16_000,
        utterance_end_ms: int = 1_000,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.utterance_end_ms = utterance_end_ms
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")

    async def transcribe(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Transcript]:
        from deepgram import AsyncDeepgramClient
        from deepgram.listen.v1.types.listen_v1results import ListenV1Results

        client: Any = AsyncDeepgramClient(api_key=self.api_key)
        async with client.listen.v1.connect(
            model=self.model,
            language=self.language,
            encoding="linear16",
            sample_rate=self.sample_rate,
            interim_results=True,
            smart_format=True,
            utterance_end_ms=str(self.utterance_end_ms),
        ) as socket:

            async def pump() -> None:
                async for f in frames:
                    await socket.send_media(f.data)
                await socket.send_finalize()

            pump_task = asyncio.create_task(pump())
            try:
                async for message in socket:
                    if not isinstance(message, ListenV1Results):
                        continue
                    alts = message.channel.alternatives if message.channel else None
                    if not alts:
                        continue
                    text = alts[0].transcript or ""
                    confidence = alts[0].confidence or 0.0
                    is_final = bool(message.is_final)
                    if not text and not is_final:
                        continue
                    yield Transcript(
                        text=text,
                        is_final=is_final,
                        confidence=confidence,
                        health=classify_health(text, confidence),
                    )
                    if message.speech_final:
                        break
            finally:
                pump_task.cancel()
                with _suppress_cancelled():
                    await pump_task


class _suppress_cancelled:
    """`await` 済みタスクの CancelledError を握り潰す小ヘルパ。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type is not None and issubclass(exc_type, asyncio.CancelledError)
