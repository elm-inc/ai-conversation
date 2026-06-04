"""対話オーケストレーション (ベンダー非依存コア)。

FSM (LISTEN→THINK→SPEAK→IDLE)。
Phase 1: ストリーミング semantic endpointing — partial transcript ごとに TurnDetector へ問い、
  COMPLETE で発話終端を確定する。相槌 (BACKCHANNEL) はターンを奪わず聞き続ける。

★不変条件 (設計レビュー反映): 応答音声は発話終端 (TurnLabel.COMPLETE) 確定後のみ出力する。
  投機生成 (LLM ドラフト) は将来 THINK 前に前倒ししてよいが、TTS への送出 = 発声は必ず
  終端確定ゲートを通す。確定前の投機音声はスピーカーに出さない。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .events import SpeechState, Transcript, TranscriptHealth, TurnLabel
from .metrics import LatencyRecorder
from .ports import AudioTransport, LLMProvider, STTProvider, TTSProvider, TurnDetector

_SENTENCE_BOUNDARY = "。．！？!?\n"


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
        - 検出器が COMPLETE を出さずに STT が尽きたら、最後の usable final で確定する
          (検出器が保守的すぎて発話を取りこぼさないためのフォールバック)。
        """
        clock = self.metrics.clock
        last_text = ""
        last_change_ms = clock()
        fallback_final: Transcript | None = None

        async for tr in self.stt.transcribe(self.transport.inbound()):
            now = clock()
            if tr.text != last_text:
                last_text = tr.text
                last_change_ms = now
            silence_ms = now - last_change_ms

            # 異常 final はゲート (投機暴走の防止)
            if tr.is_final and tr.health is TranscriptHealth.ABNORMAL:
                return None

            decision = self.turn_detector.predict(tr, silence_ms=silence_ms)
            if decision.label is TurnLabel.BACKCHANNEL:
                continue  # 相槌はターンを奪わない
            if decision.label is TurnLabel.COMPLETE and tr.is_usable:
                self.metrics.mark("stt_final")
                self.metrics.mark("end_of_speech")
                return tr
            if tr.is_final and tr.is_usable:
                fallback_final = tr

        if fallback_final is not None:
            self.metrics.mark("stt_final")
            self.metrics.mark("end_of_speech")
            return fallback_final
        return None

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
