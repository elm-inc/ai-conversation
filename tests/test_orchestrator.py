from aiconv.adapters.mock import MockLLM, MockSTT, MockTransport, MockTTS
from aiconv.adapters.turn import SilenceTurnDetector
from aiconv.core.events import SpeechState, TranscriptHealth
from aiconv.core.metrics import LatencyRecorder
from aiconv.core.orchestrator import ConversationOrchestrator, OrchestratorConfig
from aiconv.poc.run_loop import build_orchestrator, utterance_frames


def _orch(**overrides: object) -> ConversationOrchestrator:
    kwargs: dict[str, object] = dict(
        stt=MockSTT("今日はいい天気だね"),
        llm=MockLLM("そうだね。"),
        tts=MockTTS(ttfa_ms=0.0),
        turn_detector=SilenceTurnDetector(),
        transport=MockTransport(utterance_frames()),
        metrics=LatencyRecorder(),
        config=OrchestratorConfig(),
    )
    kwargs.update(overrides)
    return ConversationOrchestrator(**kwargs)  # type: ignore[arg-type]


async def test_full_turn_produces_reply_and_latency() -> None:
    orch = _orch()
    reply = await orch.run_turn()

    assert reply == "そうだね。"
    assert orch.state is SpeechState.IDLE
    turn = orch.metrics.turns[-1]
    # 体感遅延が記録され、正の値であること
    assert turn.response_latency_ms is not None
    assert turn.response_latency_ms > 0
    # first_audio は end_of_speech より後 (音声出力ゲートの不変条件)
    assert turn.marks["first_audio"] >= turn.marks["end_of_speech"]


async def test_abnormal_transcript_is_gated() -> None:
    # 健全性ゲート: 異常 transcript では発声しない
    transport = MockTransport(utterance_frames())
    orch = _orch(stt=MockSTT("あああああ", health=TranscriptHealth.ABNORMAL), transport=transport)
    reply = await orch.run_turn()

    assert reply is None
    assert transport.played == []  # 音声は一切出ていない


async def test_incomplete_turn_is_gated() -> None:
    # 終端未確定 (無音が閾値未満) なら応答音声を出さない
    transport = MockTransport(utterance_frames())
    orch = _orch(
        transport=transport,
        turn_detector=SilenceTurnDetector(threshold_ms=10_000.0),  # 事実上 COMPLETE にならない
        config=OrchestratorConfig(silence_endpoint_ms=600.0),
    )
    reply = await orch.run_turn()

    assert reply is None
    assert transport.played == []


async def test_poc_loop_runs() -> None:
    orch = build_orchestrator()
    reply = await orch.run_turn()
    assert reply
    assert orch.metrics.report() != "(no turns recorded)"
