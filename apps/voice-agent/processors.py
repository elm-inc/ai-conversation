"""自作の日本語ターンテイキングを Pipecat の FrameProcessor として再利用する。

MVP では「観測 + ログ」段階: STT の転写フレームに対し我々の FusionTurnDetector を走らせ、
ターン判定 (COMPLETE/INCOMPLETE/BACKCHANNEL/BARGE_IN) をログに出す。フレームは素通しする
ので Pipecat の VAD ベースのターン制御は壊さない。

次段で、この判定を実際のターン制御 (UserStoppedSpeaking の発火/抑制) に接続する。
我々の純粋ロジック (aiconv.core.endpointing / aiconv.adapters.turn_fusion) はそのまま流用。
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from aiconv.adapters.turn_fusion import FusionTurnDetector
from aiconv.core.events import Transcript


class JapaneseEndpointingProcessor(FrameProcessor):
    """日本語の発話完結度を観測し、ターン判定をログ化する (MVP)。

    Pipecat が AI 発話中かどうかを追えるよう、Bot の発話状態を見て during_speech を切替える。
    """

    def __init__(self) -> None:
        super().__init__()
        self._detector = FusionTurnDetector()
        self._bot_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Bot の発話状態を追う (barge-in 判定の during_speech に使う)
        cls = type(frame).__name__
        if cls == "BotStartedSpeakingFrame":
            self._bot_speaking = True
        elif cls == "BotStoppedSpeakingFrame":
            self._bot_speaking = False

        if isinstance(frame, TranscriptionFrame | InterimTranscriptionFrame):
            text = getattr(frame, "text", "") or ""
            is_final = isinstance(frame, TranscriptionFrame)
            if text.strip():
                tr = Transcript(text=text, is_final=is_final)
                decision = self._detector.predict(
                    tr, silence_ms=0.0, during_speech=self._bot_speaking
                )
                logger.info(
                    "jp-endpointing during_speech={} final={} label={} text={!r}",
                    self._bot_speaking,
                    is_final,
                    decision.label.value,
                    text,
                )

        # フレームは素通し (MVP では制御に介入しない)
        await self.push_frame(frame, direction)
