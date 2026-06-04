"""ai-conversation voice agent — 日本語キャラクター「あい」(Daily 全二重 / Pipecat Cloud)。

cascade パイプライン: DeepgramSTT(ja) → AnthropicLLM(persona) → ElevenLabsTTS(声優voice)。
Pipecat CLI の canonical テンプレート構造に準拠 (PipelineWorker / WorkerRunner /
bot(runner_args) エントリ)。ローカル開発: `uv run bot.py --transport daily`。

MVP はターンテイキングを Pipecat の VAD/割り込みに任せる。日本語 semantic endpointing
(processors.JapaneseEndpointingProcessor / aiconv) の接続は次増分。
"""

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    LLMEnablePromptCachingFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

LLM_MODEL = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
TTS_MODEL = os.getenv("TTS_MODEL") or "eleven_flash_v2_5"
STT_MODEL = os.getenv("STT_MODEL") or "nova-2"
STT_LANGUAGE = os.getenv("STT_LANGUAGE") or "ja"

# 一貫した会話人格「あい」。音声で読み上げる前提 (絵文字/記号/箇条書きを出さない)。
PERSONA = os.getenv("PERSONA_PROMPT") or (
    "あなたは親しみやすい日本語の話し相手「あい」です。"
    "砕けた自然な日本語で短めに話し、相手の話をまず受け止めてから返します。"
    "知ったかぶりをせず、分からないことは素直に分からないと言います。"
    "読み上げられるので、絵文字・顔文字・記号の羅列・箇条書き・URL は出さず、"
    "1〜2文で簡潔に、相手に話す余白を残します。"
)

# 自作差別化: 応答生成の開始時に相づち/フィラーを即発話し、LLM/TTS の待ち時間を埋める
# (Phase 1「フィラーによるレイテンシ隠蔽」を Pipecat に接続)。TTSSpeakFrame なので声優ボイス
# のまま・サンプルレート問題なし。append_to_context=False で会話履歴は汚さない。
_FILLERS = ("えーと、", "うーん、", "そうだね、", "なるほど、")


class FillerProcessor(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self._i = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        # LLM が応答を開始した瞬間 = 本応答音声が来るまでの間。相づちで埋める。
        if isinstance(frame, LLMFullResponseStartFrame) and direction == FrameDirection.DOWNSTREAM:
            phrase = _FILLERS[self._i % len(_FILLERS)]
            self._i += 1
            await self.push_frame(
                TTSSpeakFrame(phrase, append_to_context=False), FrameDirection.DOWNSTREAM
            )


async def run_bot(transport: BaseTransport) -> None:
    logger.info("Starting あい bot")

    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(
            language=STT_LANGUAGE,
            model=STT_MODEL,
            interim_results=True,
            smart_format=True,
        ),
    )

    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        model=TTS_MODEL,
        settings=ElevenLabsTTSService.Settings(voice=os.getenv("ELEVENLABS_VOICE_ID")),
    )

    llm = AnthropicLLMService(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        settings=AnthropicLLMService.Settings(model=LLM_MODEL, system_instruction=PERSONA),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            FillerProcessor(),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[],
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi: object) -> None:
        context.add_message({"role": "developer", "content": "まず一言で自己紹介して。"})
        # prompt caching を有効化 (persona/履歴をキャッシュし TTFT を下げる)
        await worker.queue_frames([LLMEnablePromptCachingFrame(enable=True), LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport: BaseTransport, client: object) -> None:
        logger.info("client disconnected, ending")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    """Pipecat Cloud / runner エントリポイント。"""
    if not os.getenv("ELEVENLABS_VOICE_ID"):
        raise RuntimeError("ELEVENLABS_VOICE_ID (声優 voice_id) が未設定です")

    match runner_args:
        case DailyRunnerArguments():
            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "あい",
                params=DailyParams(audio_in_enabled=True, audio_out_enabled=True),
            )
        case _:
            logger.error(f"Unsupported runner arguments: {type(runner_args)}")
            return

    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
