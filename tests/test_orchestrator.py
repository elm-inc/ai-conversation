from aiconv.adapters.mock import MockLLM, MockSTT, MockTransport, MockTTS, ScriptedSTT
from aiconv.adapters.turn_fusion import FusionTurnDetector
from aiconv.core.events import SpeechState, Transcript, TranscriptHealth
from aiconv.core.metrics import LatencyRecorder
from aiconv.core.orchestrator import ConversationOrchestrator, OrchestratorConfig
from aiconv.poc.run_loop import build_orchestrator, utterance_frames


def _orch(**overrides: object) -> ConversationOrchestrator:
    kwargs: dict[str, object] = dict(
        stt=MockSTT("今日はいい天気だね"),
        llm=MockLLM("そうだね。"),
        tts=MockTTS(ttfa_ms=0.0),
        turn_detector=FusionTurnDetector(),
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
    assert turn.response_latency_ms is not None
    assert turn.response_latency_ms > 0
    # first_audio は end_of_speech より後 (音声出力ゲートの不変条件)
    assert turn.marks["first_audio"] >= turn.marks["end_of_speech"]


async def test_abnormal_transcript_is_gated() -> None:
    transport = MockTransport(utterance_frames())
    orch = _orch(stt=MockSTT("あああああ", health=TranscriptHealth.ABNORMAL), transport=transport)
    reply = await orch.run_turn()

    assert reply is None
    assert transport.played == []  # 音声は一切出ていない


async def test_backchannel_does_not_take_turn() -> None:
    # 相槌の単独発話はターンを奪わない → 応答しない
    transport = MockTransport(utterance_frames())
    orch = _orch(stt=MockSTT("うん"), transport=transport)
    reply = await orch.run_turn()

    assert reply is None
    assert transport.played == []


async def test_endpoint_hint_completes_joshidome() -> None:
    # 助詞止め「〜だけど」でも音響終端ヒントが来れば COMPLETE (ターン放棄)
    transport = MockTransport(utterance_frames())
    orch = _orch(
        stt=ScriptedSTT(
            [
                Transcript("今日はいい天気", is_final=False),  # 体言止め途中 → 待つ
                Transcript("今日はいい天気だけど", is_final=True, endpoint_hint=True),
            ]
        ),
        transport=transport,
    )
    reply = await orch.run_turn()

    assert reply == "そうだね。"
    assert len(transport.played) > 0


async def test_filler_only_final_is_not_answered() -> None:
    # ストリームが COMPLETE 無しで終わっても、フィラーのみ final には応答しない
    # (codex レビュー P1: 全 final を昇格させない)
    transport = MockTransport(utterance_frames())
    orch = _orch(stt=ScriptedSTT([Transcript("えーと", is_final=True)]), transport=transport)
    reply = await orch.run_turn()

    assert reply is None
    assert transport.played == []


async def test_midlevel_final_completes_at_stream_end() -> None:
    # 体言止め等 (中程度スコア) は音響ヒント無しでも、入力終了時に最大無音とみなして応答
    transport = MockTransport(utterance_frames())
    orch = _orch(stt=ScriptedSTT([Transcript("駅前のカフェ", is_final=True)]), transport=transport)
    reply = await orch.run_turn()

    assert reply == "そうだね。"
    assert len(transport.played) > 0


async def test_poc_loop_runs() -> None:
    orch = build_orchestrator()
    reply = await orch.run_turn()
    assert reply
    assert orch.metrics.report() != "(no turns recorded)"
