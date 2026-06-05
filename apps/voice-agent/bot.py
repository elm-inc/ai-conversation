"""ai-conversation voice agent — 日本語キャラクター「あい」(Daily 全二重 / Pipecat Cloud)。

cascade パイプライン: DeepgramSTT(ja) → AnthropicLLM(persona) → ElevenLabsTTS(声優voice)。
Pipecat CLI の canonical テンプレート構造に準拠 (PipelineWorker / WorkerRunner /
bot(runner_args) エントリ)。ローカル開発: `uv run bot.py --transport daily`。

MVP はターンテイキングを Pipecat の VAD/割り込みに任せる。日本語 semantic endpointing
(processors.JapaneseEndpointingProcessor / aiconv) の接続は次増分。
"""

import os
import random

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
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
    "挨拶は会話の最初の一度だけにします。会話の途中では「おはよう」などの挨拶を繰り返さず、"
    "話題の続きから自然に話します。"
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
# フィラー発火確率 (1.0=毎ターン=現状)。「うるさい」緩和用に下げる (例 0.4 で約4割)。
FILLER_PROB = _env_float("FILLER_PROB", 1.0)

# --- ターンテイキング調整 (既定値は pipecat 既定と一致 = あい本番に無影響) ---
# VAD 発話終端待ち秒。0.2=pipecat 既定 (速いが日本語の文中ポーズで誤終端しやすい)。
# 0.6 程度にすると「えーと」等の自然な間で turn を切らずに済む。
VAD_STOP_SECS = _env_float("VAD_STOP_SECS", 0.2)
# barge-in (ボット発話中の割り込み) に要する最低語数。0=既定 (VAD の声検出で即割り込み=ハードトリガ)。
# >0 で MinWordsUserTurnStartStrategy を使用。ボット発話中は min_words 語、無音時は1語で turn 開始
# (応答性は維持しつつ、相手の一声・ノイズでの不自然な割り込みを抑える)。停止判定は Smart Turn v3 既定を維持。
TURN_MIN_WORDS = int(os.getenv("TURN_MIN_WORDS", "0") or "0")

# 会話録音 (AI同士会話の検証/コンテンツ用)。既定 off であい本番は無影響。ゆう(ローカル)で RECORD=1。
# 片側 (ゆう) で録れば room 全体 = あいの入力音声(L) + ゆうの出力音声(R) が stereo で混ざって録れる。
# ~1s ごとに raw PCM (24kHz/16-bit/2ch) を追記するので SIGINT で落ちても直前まで残る。director が wav 化。
RECORD_ENABLED = os.getenv("RECORD", "0") != "0"
RECORD_PATH = os.getenv("RECORD_PATH") or "recording.pcm"
RECORD_SAMPLE_RATE = 24000
RECORD_CHANNELS = 2
# "mix"=相手の声(webrtc)+自分を合成した stereo / "bot"=自分の出力のみ(フル品質、2拠点マージ用)。
RECORD_TRACK = os.getenv("RECORD_TRACK") or "mix"
# 2拠点録音の共通開始エポック (time.time())。bot track の先頭無音算出に使う。0 なら整列なし。
REC_T0 = _env_float("REC_T0", 0.0)

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
            # 毎ターン必発は「うるさい」ため確率発火 (FILLER_PROB)。発火時のみ語を進める。
            if FILLER_PROB < 1.0 and random.random() >= FILLER_PROB:
                return
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

    # VAD: 終端待ちを env で調整可 (既定 0.2 = pipecat 既定なら素の Analyzer)。
    vad = (
        SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS))
        if VAD_STOP_SECS != 0.2
        else SileroVADAnalyzer()
    )
    agg_kwargs: dict = {"vad_analyzer": vad}
    if TURN_MIN_WORDS > 0:
        # barge-in を最低語数でゲート (ハードトリガな不自然割り込みを抑制)。停止判定は既定維持。
        from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
            MinWordsUserTurnStartStrategy,
        )
        from pipecat.turns.user_turn_strategies import UserTurnStrategies

        agg_kwargs["user_turn_strategies"] = UserTurnStrategies(
            start=[MinWordsUserTurnStartStrategy(min_words=TURN_MIN_WORDS)]
        )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(**agg_kwargs),
    )

    processors = [transport.input(), stt, user_aggregator, llm]
    if FILLER_ENABLED:
        # 応答開始時に相づちを即発話し LLM/TTS の待ちを埋める (レイテンシ隠蔽)
        processors.append(FillerProcessor())
    processors += [tts, transport.output(), assistant_aggregator]

    audiobuffer = None
    if RECORD_ENABLED:
        # pipeline 末尾に置くと入力(相手の声)と出力(自分の声)の両 RawFrame を捕捉できる。
        import time as _time

        from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

        audiobuffer = AudioBufferProcessor(
            sample_rate=RECORD_SAMPLE_RATE, num_channels=RECORD_CHANNELS, buffer_size=48000
        )
        processors.append(audiobuffer)

        if RECORD_TRACK == "bot":
            # 自分の出力 (フル品質) のみ録音。相手の声 (webrtc 劣化) は捨てる。先頭無音
            # (= 最初の発話 wall - REC_T0) を .meta に残し、director が共通 T0 で 2 トラックを整列。
            _first_bot_wall = [0.0]

            @audiobuffer.event_handler("on_track_audio_data")
            async def _on_track(
                buf: object, user_audio: bytes, bot_audio: bytes, sample_rate: int, ch: int
            ) -> None:
                if not bot_audio:
                    return
                if _first_bot_wall[0] == 0.0:
                    _first_bot_wall[0] = _time.time()
                    lead = max(0.0, _first_bot_wall[0] - REC_T0) if REC_T0 else 0.0
                    with open(RECORD_PATH + ".meta", "w") as mf:
                        mf.write(str(lead))
                with open(RECORD_PATH, "ab") as f:  # crash-safe append
                    f.write(bytes(bot_audio))
        else:

            @audiobuffer.event_handler("on_audio_data")
            async def _on_audio_data(
                buf: object, audio: bytes, sample_rate: int, num_channels: int
            ) -> None:
                # crash-safe: コールバック毎に open/append/close (SIGINT で落ちても直前まで残る)
                with open(RECORD_PATH, "ab") as f:
                    f.write(bytes(audio))

    pipeline = Pipeline(processors)

    _rec_started = [False]  # one-shot ガード (codex P2: 2回目の callback で先頭を破棄しない)

    async def _maybe_start_recording() -> None:
        if not (RECORD_ENABLED and audiobuffer is not None) or _rec_started[0]:
            return
        _rec_started[0] = True
        os.makedirs(os.path.dirname(RECORD_PATH) or ".", exist_ok=True)  # codex P2: 親dir作成
        open(RECORD_PATH, "wb").close()  # 録音ファイルを初期化
        try:
            os.remove(RECORD_PATH + ".meta")
        except OSError:
            pass
        await audiobuffer.start_recording()
        logger.info("recording started → {} (track={})", RECORD_PATH, RECORD_TRACK)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[],
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi: object) -> None:
        # kickoff 時のみ自分から話し始める。NOTE: developer 指示が context に残る軽微な問題
        # (codex P2) は既知だが、TTSSpeakFrame 化は応答不能の回帰を招いたため保留中。
        await _maybe_start_recording()
        if not KICKOFF:
            return
        context.add_message({"role": "developer", "content": KICKOFF_PROMPT})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport: BaseTransport, participant: object) -> None:
        # bot-to-bot: 相手 (あい) が居る/入ってきたら口火を切る (KICKOFF_ON_JOIN 時のみ)
        await _maybe_start_recording()
        if not KICKOFF_ON_JOIN:
            return
        logger.info("participant joined → kickoff")
        context.add_message({"role": "developer", "content": KICKOFF_PROMPT})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport: BaseTransport, client: object) -> None:
        # NOTE (codex P2, 既知の制限): これは「任意の参加者離脱」の alias。あい単独ブラウザ用途では
        # ユーザー離脱で即終了するのが正しい (孤児セッション/課金を防ぐ) が、bot-to-bot + 聴衆 join 時は
        # 聴衆がタブを閉じただけで会話全体が止まる。聴衆コンテンツ (Phase D) 着手時に「残り参加者が
        # 0 のときだけ終了」へ修正する (要 multi-party live 検証なので deploy→検証 を別サイクルで)。
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
