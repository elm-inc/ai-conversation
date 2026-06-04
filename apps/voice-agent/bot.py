"""Pipecat Cloud ボット — Daily 全二重の日本語キャラクター対話 (MVP)。

パイプライン:
  DailyTransport.input() → DeepgramSTT(ja) → JapaneseEndpointingProcessor(観測)
   → user_aggregator → AnthropicLLM(persona) → ElevenLabsTTS(声優voice)
   → DailyTransport.output() → assistant_aggregator
+ Silero VAD (発話区間検出) / Pipecat の割り込み制御 (allow_interruptions)

Pipecat Cloud は `bot(args: DailySessionArguments)` を呼び、Daily ルーム/トークンを渡す。
ローカル開発は `pipecat.runner` 経由 (README 参照)。
"""

from __future__ import annotations

import os

from loguru import logger
from persona import LLM_MODEL, PERSONA, STT_LANGUAGE, STT_MODEL, TTS_MODEL, VOICE_ID
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from processors import JapaneseEndpointingProcessor


def _build_pipeline(transport: DailyTransport) -> PipelineTask:
    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        live_options=LiveOptions(
            language=STT_LANGUAGE,
            model=STT_MODEL,
            interim_results=True,
            smart_format=True,
        ),
    )
    llm = AnthropicLLMService(api_key=os.environ["ANTHROPIC_API_KEY"], model=LLM_MODEL)
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        voice_id=VOICE_ID,
        model=TTS_MODEL,
    )

    context = LLMContext(messages=[{"role": "system", "content": PERSONA}])
    aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            JapaneseEndpointingProcessor(),
            aggregator.user(),
            llm,
            tts,
            transport.output(),
            aggregator.assistant(),
        ]
    )
    return PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True, enable_metrics=True),
    )


async def bot(args: object) -> None:
    """Pipecat Cloud / runner のエントリポイント。args は DailySessionArguments。"""
    if not VOICE_ID:
        raise RuntimeError("ELEVENLABS_VOICE_ID (声優 voice_id) が未設定です")

    room_url = args.room_url  # type: ignore[attr-defined]
    token = args.token  # type: ignore[attr-defined]

    transport = DailyTransport(
        room_url,
        token,
        "あい",
        DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )
    task = _build_pipeline(transport)

    @transport.event_handler("on_first_participant_joined")
    async def _on_join(transport: DailyTransport, participant: dict) -> None:
        logger.info("participant joined: {}", participant.get("id"))
        # 最初の挨拶を生成させる
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_participant_left")
    async def _on_left(transport: DailyTransport, participant: dict, reason: str) -> None:
        logger.info("participant left ({}), ending", reason)
        await task.cancel()

    runner = PipelineRunner(handle_sigint=getattr(args, "handle_sigint", False))
    await runner.run(task)
