"""mock アダプタ — 実 API なしで PoC cascaded ループをオフライン実行する足場。

各 mock は対応する Port Protocol を満たす。遅延パラメータでレイテンシ harness を検証できる。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..core.events import AudioFrame, TokenChunk, Transcript, TranscriptHealth
from ..core.ports import Capability


class MockTransport:
    """事前に用意した inbound フレームを流し、再生フレームを記録する。"""

    def __init__(self, frames: list[AudioFrame]) -> None:
        self._frames = frames
        self.played: list[AudioFrame] = []
        self.stopped = False

    async def inbound(self) -> AsyncIterator[AudioFrame]:
        for f in self._frames:
            yield f

    async def play(self, frame: AudioFrame) -> None:
        self.played.append(frame)

    async def stop_playback(self) -> None:
        self.stopped = True


class MockSTT:
    """inbound を消費し、設定した固定テキストの final transcript を返す。"""

    capabilities = Capability(streaming_partials=True, languages=("ja",), notes="mock")

    def __init__(
        self,
        text: str,
        *,
        health: TranscriptHealth = TranscriptHealth.OK,
        latency_ms: float = 0.0,
    ) -> None:
        self.text = text
        self.health = health
        self.latency_ms = latency_ms

    async def transcribe(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Transcript]:
        async for _ in frames:
            pass
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
        yield Transcript(self.text, is_final=True, confidence=0.95, health=self.health)


class ScriptedSTT:
    """事前に用意した Transcript 列を順に流す。ストリーミング endpointing のテスト用。"""

    capabilities = Capability(streaming_partials=True, languages=("ja",), notes="scripted")

    def __init__(self, transcripts: list[Transcript], *, gap_ms: float = 0.0) -> None:
        self._transcripts = transcripts
        self.gap_ms = gap_ms

    async def transcribe(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Transcript]:
        async for _ in frames:
            pass
        for tr in self._transcripts:
            if self.gap_ms:
                await asyncio.sleep(self.gap_ms / 1000.0)
            yield tr


class MockLLM:
    """固定応答を文字単位でストリーミングする (TTFT/トークン間隔を模擬)。"""

    capabilities = Capability(streaming_partials=True, notes="mock")

    def __init__(
        self,
        reply: str = "うん、そうだね。",
        *,
        ttft_ms: float = 120.0,
        per_token_ms: float = 8.0,
    ) -> None:
        self.reply = reply
        self.ttft_ms = ttft_ms
        self.per_token_ms = per_token_ms

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        system: str = "",
        cache_hint: bool = True,
    ) -> AsyncIterator[TokenChunk]:
        await asyncio.sleep(self.ttft_ms / 1000.0)
        chars = list(self.reply)
        last = len(chars) - 1
        for i, ch in enumerate(chars):
            if i:
                await asyncio.sleep(self.per_token_ms / 1000.0)
            yield TokenChunk(ch, index=i, is_first=(i == 0), is_last=(i == last))


class MockTTS:
    """文チャンクごとに無音 PCM フレームを返す。interrupt 対応。"""

    capabilities = Capability(supports_interrupt=True, flush_latency_ms=20.0, notes="mock silence")

    def __init__(
        self,
        *,
        ttfa_ms: float = 150.0,
        samples_per_frame: int = 160,
        frames_per_chunk: int = 1,
        frame_gap_ms: float = 0.0,
    ) -> None:
        self.ttfa_ms = ttfa_ms
        self._silence = b"\x00\x00" * samples_per_frame
        self.frames_per_chunk = frames_per_chunk
        self.frame_gap_ms = frame_gap_ms
        self._interrupted = False

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioFrame]:
        first = True
        async for _chunk in text_chunks:
            if self._interrupted:
                break
            if first:
                await asyncio.sleep(self.ttfa_ms / 1000.0)
                first = False
            for _ in range(self.frames_per_chunk):
                if self._interrupted:
                    break
                yield AudioFrame(data=self._silence, ts_ms=0.0)
                if self.frame_gap_ms:
                    await asyncio.sleep(self.frame_gap_ms / 1000.0)

    async def interrupt(self) -> None:
        self._interrupted = True
