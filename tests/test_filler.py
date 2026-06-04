from aiconv.adapters.filler import MockFiller
from aiconv.adapters.mock import MockLLM, MockSTT, MockTransport, MockTTS
from aiconv.adapters.turn_fusion import FusionTurnDetector
from aiconv.core.metrics import LatencyRecorder
from aiconv.core.orchestrator import ConversationOrchestrator, OrchestratorConfig
from aiconv.poc.run_loop import utterance_frames


def _orch(**overrides: object) -> ConversationOrchestrator:
    kwargs: dict[str, object] = dict(
        stt=MockSTT("今日はいい天気だね"),
        # TTFT を遅くしてフィラーが流れる猶予を作る
        llm=MockLLM("そうだね。", ttft_ms=80.0),
        tts=MockTTS(ttfa_ms=0.0),
        turn_detector=FusionTurnDetector(),
        transport=MockTransport(utterance_frames()),
        metrics=LatencyRecorder(),
        config=OrchestratorConfig(),
    )
    kwargs.update(overrides)
    return ConversationOrchestrator(**kwargs)  # type: ignore[arg-type]


async def test_filler_hides_latency_first_audio_before_response() -> None:
    transport = MockTransport(utterance_frames())
    orch = _orch(filler=MockFiller(frames=10), transport=transport)
    reply = await orch.run_turn()

    assert reply == "そうだね。"
    turn = orch.metrics.turns[-1]
    # フィラーで最初の音が早く出て、本応答音声はそれ以降
    assert turn.marks["first_audio"] <= turn.marks["first_response_audio"]
    # 体感遅延(first_audio) <= 本応答遅延(first_response_audio)
    assert turn.response_latency_ms is not None
    assert turn.response_audio_latency_ms is not None
    assert turn.response_latency_ms <= turn.response_audio_latency_ms
    # フィラー + 本応答の両方が再生された (本応答は1文=1フレーム)
    assert len(transport.played) > 1


async def test_no_filler_first_audio_equals_response() -> None:
    transport = MockTransport(utterance_frames())
    orch = _orch(filler=None, transport=transport)
    reply = await orch.run_turn()

    assert reply == "そうだね。"
    turn = orch.metrics.turns[-1]
    # フィラー無しなら first_audio と first_response_audio は実質同一イベント (差は無視できる)
    assert turn.marks["first_audio"] <= turn.marks["first_response_audio"]
    assert turn.marks["first_response_audio"] - turn.marks["first_audio"] < 1.0


async def test_filler_stops_when_response_ready() -> None:
    # フィラーは大量に用意しても、本応答が来たら打ち切られる (無限に流れない)
    transport = MockTransport(utterance_frames())
    orch = _orch(filler=MockFiller(frames=10_000), transport=transport)
    reply = await orch.run_turn()

    assert reply == "そうだね。"
    # 10000 フレーム全部は流れない (本応答到着で停止)
    assert len(transport.played) < 10_000
