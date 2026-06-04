"""対話オーケストレーション (ベンダー非依存コア)。

FSM (LISTEN→THINK→SPEAK→IDLE)。
Phase 1: ストリーミング semantic endpointing — partial transcript ごとに TurnDetector へ問い、
  COMPLETE で発話終端を確定する。相槌 (BACKCHANNEL) はターンを奪わず聞き続ける。

★不変条件 (設計レビュー反映): 応答音声は発話終端 (TurnLabel.COMPLETE) 確定後のみ出力する。
  投機生成 (LLM ドラフト) は将来 THINK 前に前倒ししてよいが、TTS への送出 = 発声は必ず
  終端確定ゲートを通す。確定前の投機音声はスピーカーに出さない。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .events import AudioFrame, SpeechState, Transcript, TranscriptHealth, TurnLabel
from .metrics import LatencyRecorder
from .ports import (
    AudioTransport,
    FillerProvider,
    LLMProvider,
    STTProvider,
    TTSProvider,
    TurnDetector,
)

_SENTENCE_BOUNDARY = "。．！？!?\n"
# 入力ストリーム終了は確定的な発話終了。検出器に最大無音として最終判断を仰ぐための値。
_END_OF_STREAM_SILENCE_MS = 10_000.0


@dataclass
class OrchestratorConfig:
    system_prompt: str = ""


@dataclass
class ConversationOrchestrator:
    stt: STTProvider
    llm: LLMProvider
    tts: TTSProvider
    turn_detector: TurnDetector
    transport: AudioTransport
    filler: FillerProvider | None = None
    metrics: LatencyRecorder = field(default_factory=LatencyRecorder)
    config: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    state: SpeechState = SpeechState.IDLE

    async def run_turn(self) -> str | None:
        """1 ターン: 聞く → 終端判定 → 考える → 話す。応答テキストを返す (無応答なら None)。"""
        self.metrics.begin_turn()

        # --- LISTEN: ストリーミング endpointing で発話終端を確定 ---
        self.state = SpeechState.LISTEN
        final = await self._listen()
        if final is None or not final.is_usable:
            self.state = SpeechState.IDLE
            return None

        # --- THINK → SPEAK: 終端確定後のみ。LLM 出力を文単位で TTS へ ---
        return await self._respond(final)

    async def _listen(self) -> Transcript | None:
        """partial transcript ごとに TurnDetector へ問い、COMPLETE で終端を確定する。

        - BACKCHANNEL (相槌) はターンを奪わず聞き続ける。
        - 異常 final は健全性ゲートで弾く。
        - silence_ms は「最後にテキストが変わってからの経過」。更新前に計算するので、
          終端イベント直前の沈黙が検出器に渡る (無音ベースの完了判定が効く)。
        - 入力が尽きたら確定的な発話終了とみなし、最大無音で検出器に最終判断を仰ぐ。
          フィラー/断片/相槌は INCOMPLETE/BACKCHANNEL のままなので応答しない。
        """
        clock = self.metrics.clock
        last_text = ""
        last_change_ms = clock()
        last_final: Transcript | None = None

        async for tr in self.stt.transcribe(self.transport.inbound()):
            now = clock()
            silence_ms = now - last_change_ms  # 更新前に計算 (直前の沈黙を保つ)
            if tr.text != last_text:
                last_text = tr.text
                last_change_ms = now

            # 異常 final はゲート (投機暴走の防止)
            if tr.is_final and tr.health is TranscriptHealth.ABNORMAL:
                return None

            decision = self.turn_detector.predict(tr, silence_ms=silence_ms)
            if decision.label is TurnLabel.BACKCHANNEL:
                continue  # 相槌はターンを奪わない
            if decision.label is TurnLabel.COMPLETE and tr.is_usable:
                return self._confirm_end(tr)
            if tr.is_final and tr.is_usable:
                last_final = tr

        # 入力ストリーム終了 = 確定的な発話終了。最大無音で検出器に最終判断を仰ぐ。
        if last_final is not None:
            decision = self.turn_detector.predict(last_final, silence_ms=_END_OF_STREAM_SILENCE_MS)
            if decision.label is TurnLabel.COMPLETE:
                return self._confirm_end(last_final)
        return None

    def _confirm_end(self, tr: Transcript) -> Transcript:
        self.metrics.mark("stt_final")
        self.metrics.mark("end_of_speech")
        return tr

    async def _respond(self, final: Transcript) -> str:
        self.state = SpeechState.THINK
        messages = [{"role": "user", "content": final.text}]
        reply_parts: list[str] = []

        async def token_texts() -> AsyncIterator[str]:
            async for tok in self.llm.generate(messages, system=self.config.system_prompt):
                if tok.is_first:
                    self.metrics.mark("llm_ttft")
                reply_parts.append(tok.text)
                yield tok.text

        # 本応答音声をバックグラウンドで生成しキューへ (LLM→文チャンク→TTS)
        response_q: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for frame in self.tts.synthesize(self._sentences(token_texts())):
                    await response_q.put(frame)
            finally:
                await response_q.put(None)  # sentinel (例外時も必ず終端)

        self.state = SpeechState.SPEAK
        producer = asyncio.create_task(produce())
        played_any = False
        try:
            # レイテンシ隠蔽: 本応答が用意できるまで相槌/フィラーを流す
            if self.filler is not None:
                async for f in self.filler.filler():
                    if not response_q.empty():  # 本応答が来たらフィラー停止
                        break
                    if not played_any:
                        self.metrics.mark("first_audio")
                        played_any = True
                    await self.transport.play(f)

            # 本応答を再生
            first_response = False
            while True:
                frame = await response_q.get()
                if frame is None:
                    break
                if not played_any:  # フィラーが無かった場合はここが最初の音
                    self.metrics.mark("first_audio")
                    played_any = True
                if not first_response:
                    self.metrics.mark("first_response_audio")
                    first_response = True
                await self.transport.play(frame)
        finally:
            await producer

        self.state = SpeechState.IDLE
        return "".join(reply_parts)

    @staticmethod
    async def _sentences(tokens: AsyncIterator[str]) -> AsyncIterator[str]:
        """トークン列を文/節境界でチャンク化し、即座に下流 (TTS) へ流す。"""
        buf = ""
        async for t in tokens:
            buf += t
            if buf and buf[-1] in _SENTENCE_BOUNDARY:
                yield buf
                buf = ""
        if buf.strip():
            yield buf
