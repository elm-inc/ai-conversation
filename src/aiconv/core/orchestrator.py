"""対話オーケストレーション (ベンダー非依存コア)。

Phase 0: 素朴な無音終端で cascaded ループを回す最小 FSM (LISTEN→THINK→SPEAK→IDLE)。
Phase 1 以降で semantic endpointing・barge-in・先読みを足す。

★不変条件 (設計レビュー反映): 応答音声は発話終端 (TurnLabel.COMPLETE) 確定後のみ出力する。
  投機生成 (LLM ドラフト) は将来 THINK 前に前倒ししてよいが、TTS への送出 = 発声は必ず
  終端確定ゲートを通す。確定前の投機音声はスピーカーに出さない。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .events import SpeechState, Transcript
from .metrics import LatencyRecorder
from .ports import AudioTransport, LLMProvider, STTProvider, TTSProvider, TurnDetector

_SENTENCE_BOUNDARY = "。．！？!?\n"


@dataclass
class OrchestratorConfig:
    system_prompt: str = ""
    silence_endpoint_ms: float = 600.0  # 素朴な無音終端 (Phase 1 で semantic 化)


@dataclass
class ConversationOrchestrator:
    stt: STTProvider
    llm: LLMProvider
    tts: TTSProvider
    turn_detector: TurnDetector
    transport: AudioTransport
    metrics: LatencyRecorder = field(default_factory=LatencyRecorder)
    config: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    state: SpeechState = SpeechState.IDLE

    async def run_turn(self) -> str | None:
        """1 ターン: 聞く → 終端判定 → 考える → 話す。応答テキストを返す (無応答なら None)。"""
        rec = self.metrics
        rec.begin_turn()

        # --- LISTEN: STT で発話を確定 ---
        self.state = SpeechState.LISTEN
        final = await self._listen()
        if final is None:
            self.state = SpeechState.IDLE
            return None

        # 健全性ゲート: 異常/空 transcript では応答しない (投機暴走の防止)
        if not final.is_usable:
            self.state = SpeechState.IDLE
            return None

        # 終端ゲート: COMPLETE 以外は応答音声を出さない (★不変条件)
        decision = self.turn_detector.predict(final, silence_ms=self.config.silence_endpoint_ms)
        if not decision.is_complete:
            self.state = SpeechState.IDLE
            return None

        # --- THINK → SPEAK: 終端確定後のみ。LLM 出力を文単位で TTS へ ---
        return await self._respond(final)

    async def _listen(self) -> Transcript | None:
        final: Transcript | None = None
        async for tr in self.stt.transcribe(self.transport.inbound()):
            if tr.is_final:
                final = tr
                self.metrics.mark("stt_final")
                # Phase 0 の素朴な扱い: STT final ≒ 発話終了
                self.metrics.mark("end_of_speech")
        return final

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

        self.state = SpeechState.SPEAK
        first_audio = False
        async for frame in self.tts.synthesize(self._sentences(token_texts())):
            if not first_audio:
                self.metrics.mark("first_audio")
                first_audio = True
            await self.transport.play(frame)

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
