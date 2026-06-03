"""Pipecat AudioTransport アダプタ — スケルトン。

Pipecat v1.0 の frame processor pipeline を AudioTransport ポート背後に隠す。
WebRTC 入出力を core.events.AudioFrame に正規化する (交換コストは高めなので慎重に選定)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..core.events import AudioFrame


class PipecatTransport:
    def __init__(self, *, sample_rate: int = 16_000) -> None:
        self.sample_rate = sample_rate

    async def inbound(self) -> AsyncIterator[AudioFrame]:
        raise NotImplementedError(
            "Phase 0 スケルトン: Pipecat pipeline から AudioFrame に正規化する (AIC-1 次段)"
        )
        yield

    async def play(self, frame: AudioFrame) -> None:
        raise NotImplementedError("Phase 0 スケルトン (AIC-1 次段)")

    async def stop_playback(self) -> None:
        raise NotImplementedError("Phase 0 スケルトン (AIC-1 次段)")
