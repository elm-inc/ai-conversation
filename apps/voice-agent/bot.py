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
# 高品質モデル (抑揚が自然)。低遅延優先なら eleven_flash_v2_5。secret で上書き可。
TTS_MODEL = os.getenv("TTS_MODEL") or "eleven_multilingual_v2"
STT_MODEL = os.getenv("STT_MODEL") or "nova-2"
STT_LANGUAGE = os.getenv("STT_LANGUAGE") or "ja"


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v else default


# 声の一貫性 (発話毎のトーン/音量ブレ抑制)。stability 高いほど安定だが平板になりやすい (中庸推奨)。
# secret で上書きできるので、リビルド無しで振れる。
TTS_STABILITY = _env_float("TTS_STABILITY", 0.5)
TTS_SIMILARITY = _env_float("TTS_SIMILARITY", 0.8)
TTS_SPEAKER_BOOST = os.getenv("TTS_SPEAKER_BOOST", "1") != "0"

# 一貫した会話人格「あい」。音声で読み上げる前提 (絵文字/記号/箇条書きを出さない)。
PERSONA = os.getenv("PERSONA_PROMPT") or (
    "あなたは親しみやすい日本語の話し相手「あい」です。"
    "砕けた自然な日本語で短めに話し、相手の話をまず受け止めてから返します。"
    "知ったかぶりをせず、分からないことは素直に分からないと言います。"
    "読み上げられるので、絵文字・顔文字・記号の羅列・箇条書き・URL は出さず、"
    "1〜2文で簡潔に、相手に話す余白を残します。"
)

# --- 役割パラメータ (AIC-7: あい / interlocutor を同一コードで生やす) ---
# すべて env 未設定時は「あい」の v0.4 既定と完全一致する。
AGENT_NAME = os.getenv("AGENT_NAME") or "あい"  # Daily 表示名
SCENARIO = os.getenv("SCENARIO") or ""  # 目標駆動 improv。設定時は system に追記 (interlocutor 用)
SYSTEM_INSTRUCTION = PERSONA + (f"\n\n# シナリオ・目標\n{SCENARIO}" if SCENARIO else "")
KICKOFF = os.getenv("KICKOFF", "1") != "0"  # RTVI client 接続時に話し始めるか (ブラウザ用)
KICKOFF_PROMPT = os.getenv("KICKOFF_PROMPT") or "まず一言で自己紹介して。"
# bot-to-bot 用: 参加者 join を口火に発話 (あい既定 0 で無影響。interlocutor で 1)
KICKOFF_ON_JOIN = os.getenv("KICKOFF_ON_JOIN", "0") != "0"
# フィラー (あいづちでレイテンシ隠蔽) を pipeline に挟むか。既定 off で無影響、env で有効化。
FILLER_ENABLED = os.getenv("FILLER", "0") != "0"

# 自作差別化: 応答生成の開始時に相づち/フィラーを即発話し、LLM/TTS の待ち時間を埋める。
# ★現状 pipeline には未接続 (次増分で接続予定)。TTSSpeakFrame なので声優ボイスのまま・
# サンプルレート問題なし。append_to_context=False で会話履歴は汚さない。
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
    logger.info("Starting {} bot", AGENT_NAME)

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
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID"),
            stability=TTS_STABILITY,
            similarity_boost=TTS_SIMILARITY,
            use_speaker_boost=TTS_SPEAKER_BOOST,
        ),
    )

    llm = AnthropicLLMService(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        settings=AnthropicLLMService.Settings(
            model=LLM_MODEL, system_instruction=SYSTEM_INSTRUCTION
        ),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    processors = [transport.input(), stt, user_aggregator, llm]
    if FILLER_ENABLED:
        # 応答開始時に相づちを即発話し LLM/TTS の待ちを埋める (レイテンシ隠蔽)
        processors.append(FillerProcessor())
    processors += [tts, transport.output(), assistant_aggregator]
    pipeline = Pipeline(processors)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[],
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi: object) -> None:
        # kickoff 時のみ自分から話し始める。NOTE: developer 指示が context に残る軽微な問題
        # (codex P2) は既知だが、TTSSpeakFrame 化は応答不能の回帰を招いたため保留中。
        if not KICKOFF:
            return
        context.add_message({"role": "developer", "content": KICKOFF_PROMPT})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport: BaseTransport, participant: object) -> None:
        # bot-to-bot: 相手 (あい) が居る/入ってきたら口火を切る (KICKOFF_ON_JOIN 時のみ)
        if not KICKOFF_ON_JOIN:
            return
        logger.info("participant joined → kickoff")
        context.add_message({"role": "developer", "content": KICKOFF_PROMPT})
        await worker.queue_frames([LLMRunFrame()])

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
                AGENT_NAME,
                params=DailyParams(audio_in_enabled=True, audio_out_enabled=True),
            )
        case _:
            logger.error(f"Unsupported runner arguments: {type(runner_args)}")
            return

    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
