"""PoC cascaded ループ — mock アダプタで end-to-end を回しレイテンシを表示する。

実プロバイダ未接続でもオフラインで動く。Phase 0 のベースライン計測の足場。

    uv run python -m aiconv.poc.run_loop
"""

from __future__ import annotations

import asyncio

from ..adapters.filler import MockFiller
from ..adapters.mock import MockLLM, MockSTT, MockTransport, MockTTS
from ..adapters.turn_fusion import FusionTurnDetector
from ..core.events import AudioFormat, AudioFrame
from ..core.metrics import LatencyRecorder
from ..core.orchestrator import ConversationOrchestrator, OrchestratorConfig


def utterance_frames(*, duration_ms: int = 1000, frame_ms: int = 20) -> list[AudioFrame]:
    """1 発話分のダミー音声フレーム列を生成する。"""
    fmt = AudioFormat()
    samples = int(fmt.sample_rate * frame_ms / 1000)
    data = b"\x00\x00" * samples
    n = duration_ms // frame_ms
    return [AudioFrame(data=data, ts_ms=i * frame_ms, seq=i, fmt=fmt) for i in range(n)]


def build_orchestrator() -> ConversationOrchestrator:
    return ConversationOrchestrator(
        stt=MockSTT("今日はいい天気だね"),
        llm=MockLLM("そうだね、散歩日和だ。"),
        tts=MockTTS(),
        turn_detector=FusionTurnDetector(),
        filler=MockFiller(),
        transport=MockTransport(utterance_frames()),
        metrics=LatencyRecorder(),
        config=OrchestratorConfig(system_prompt="あなたは親しみやすい相棒。"),
    )


async def main() -> None:
    orch = build_orchestrator()
    reply = await orch.run_turn()
    print(f"reply: {reply}")
    print(orch.metrics.report())


if __name__ == "__main__":
    asyncio.run(main())
