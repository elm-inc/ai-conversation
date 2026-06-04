import asyncio
from collections.abc import AsyncIterator

from aiconv.adapters.mock import MockLLM, MockSTT, MockTransport, MockTTS
from aiconv.adapters.turn_fusion import FusionTurnDetector
from aiconv.core.events import Transcript, TurnLabel
from aiconv.core.metrics import LatencyRecorder
from aiconv.core.orchestrator import ConversationOrchestrator, OrchestratorConfig
from aiconv.poc.run_loop import utterance_frames


async def _user_stream(events: list[tuple[float, str]]) -> AsyncIterator[Transcript]:
    """(遅延ms, テキスト) を順に発話するユーザー音声ストリーム。"""
    for delay_ms, text in events:
        await asyncio.sleep(delay_ms / 1000.0)
        yield Transcript(text, is_final=True)


def _orch(transport: MockTransport) -> ConversationOrchestrator:
    return ConversationOrchestrator(
        stt=MockSTT("質問なんだけどさ"),
        llm=MockLLM("ええとね、それはとても良い質問で。"),
        # 30 フレームを 10ms 間隔で = ~300ms の再生 (割り込みが途中で刺さる猶予)
        tts=MockTTS(ttfa_ms=0.0, frames_per_chunk=30, frame_gap_ms=10.0),
        turn_detector=FusionTurnDetector(),
        transport=transport,
        metrics=LatencyRecorder(),
        config=OrchestratorConfig(),
    )


async def test_bargein_interrupts_playback() -> None:
    transport = MockTransport(utterance_frames())
    orch = _orch(transport)
    # 再生中 (80ms 時点) に実質的な割り込み
    reply = await orch.run_turn(user_stream=_user_stream([(80.0, "ちょっと待って")]))

    assert orch.interrupted is True
    assert orch.interrupting_text == "ちょっと待って"
    assert transport.stopped is True  # stop_playback が呼ばれた
    assert len(transport.played) < 30  # 全フレーム再生される前に止まった
    assert reply is not None  # 途中までの応答テキストは返る


async def test_backchannel_does_not_interrupt() -> None:
    transport = MockTransport(utterance_frames())
    orch = _orch(transport)
    # 再生中の「うん」は相槌 → 中断しない
    reply = await orch.run_turn(user_stream=_user_stream([(80.0, "うん")]))

    assert reply == "ええとね、それはとても良い質問で。"
    assert orch.interrupted is False
    assert transport.stopped is False
    assert len(transport.played) == 30  # 最後まで再生された


async def test_no_user_stream_completes_normally() -> None:
    transport = MockTransport(utterance_frames())
    orch = _orch(transport)
    reply = await orch.run_turn()  # 全二重なし

    assert reply == "ええとね、それはとても良い質問で。"
    assert orch.interrupted is False
    assert len(transport.played) == 30


def test_detector_bargein_classification() -> None:
    d = FusionTurnDetector()
    # 発話中: 相槌は BACKCHANNEL、割り込み語/実質発話は BARGE_IN
    assert d.predict(Transcript("うん", is_final=False), silence_ms=0,
                     during_speech=True).label is TurnLabel.BACKCHANNEL
    assert d.predict(Transcript("いや、ちがう", is_final=False), silence_ms=0,
                     during_speech=True).label is TurnLabel.BARGE_IN
    assert d.predict(Transcript("そうだね", is_final=False), silence_ms=0,
                     during_speech=True).label is TurnLabel.BARGE_IN
