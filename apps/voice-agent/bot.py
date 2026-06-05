"""ai-conversation voice agent — 日本語キャラクター「あい」(Daily 全二重 / Pipecat Cloud)。

cascade パイプライン: DeepgramSTT(ja) → AnthropicLLM(persona) → ElevenLabsTTS(声優voice)。
Pipecat CLI の canonical テンプレート構造に準拠 (PipelineWorker / WorkerRunner /
bot(runner_args) エントリ)。ローカル開発: `uv run bot.py --transport daily`。

MVP はターンテイキングを Pipecat の VAD/割り込みに任せる。日本語 semantic endpointing
(processors.JapaneseEndpointingProcessor / aiconv) の接続は次増分。
"""

import asyncio
import json
import os
import random
import urllib.request

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
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
    "相手の発話が断片的・意味不明・聞き取れないときは、勝手に解釈して話を進めず、"
    "軽く聞き返すか自然に受け流し、文脈に無い単語を拾いません。"
    "口調は最後まで一貫させ、敬語とタメ口を混ぜません。"
    "毎ターン質問で締めず、相づちや感想で終える番も作ります。"
    "相手がまだ言っていない内容を勝手に要約・称賛しません。"
    "読み上げられるので、絵文字・顔文字・記号の羅列・箇条書き・URL は出さず、"
    "1〜2文で簡潔に、相手に話す余白を残します。"
)

# --- 役割パラメータ (AIC-7: あい / interlocutor を同一コードで生やす) ---
# すべて env 未設定時は「あい」の v0.4 既定と完全一致する。
AGENT_NAME = os.getenv("AGENT_NAME") or "あい"  # Daily 表示名
SCENARIO = os.getenv("SCENARIO") or ""  # 目標駆動 improv。設定時は system に追記 (interlocutor 用)
# テーマ展開 (record_conversation) で生成: STT 辞書ブースト語 (カンマ区切り) と事実グラウンディング brief。
STT_KEYTERMS = os.getenv("STT_KEYTERMS") or ""
KNOWLEDGE_BRIEF = os.getenv("KNOWLEDGE_BRIEF") or ""
SYSTEM_INSTRUCTION = (
    PERSONA
    + (f"\n\n# シナリオ・目標\n{SCENARIO}" if SCENARIO else "")
    + (f"\n\n# 参考知識 (事実を取り違えない)\n{KNOWLEDGE_BRIEF}" if KNOWLEDGE_BRIEF else "")
)
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
# 診断用 (既定 off): 設定時、TTS 出力フレームを無加工で連結保存。AudioBufferProcessor の
# 壁時計ベース無音再構成を介さない「素の TTS」基準を採り、録音のブツブツの切り分けに使う。
RECORD_RAW = os.getenv("RECORD_RAW") or ""
# 初回応答のコールドスタート緩和: 起動時に最小の LLM 呼び出しでモデルを温める (既定 on)。
PREWARM = os.getenv("PREWARM", "1") != "0"
# ⚠ RECORD_SAMPLE_RATE は別 app の record_conversation.py `SR` と必ず一致させること
# (raw PCM を書く側/wav 化する側で食い違うと録音が再生不能になる)。別 package のため import 不可。
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


class RawTTSTap(FrameProcessor):
    """TTS 出力 (OutputAudioRawFrame) を無加工で連結保存する診断用タップ。

    AudioBufferProcessor の壁時計ベース無音再構成を通さない「素の TTS」を得て、録音の
    ブツブツが TTS ストリーミング起因か録音再構成起因かを切り分ける。
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._sr_written = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame):
            if not self._sr_written:
                with open(self._path + ".sr", "w") as f:
                    f.write(str(frame.sample_rate))
                self._sr_written = True
            with open(self._path, "ab") as f:
                f.write(frame.audio)
        await self.push_frame(frame, direction)


_BG_TASKS: set = set()  # fire-and-forget タスクの GC 防止用 (背景 prewarm 等)


async def _prewarm_llm() -> None:
    """初回応答のコールドスタート緩和: 最小の LLM 呼び出しでモデルを温める。

    codex P1: 起動を block しないよう背景タスクで実行し、タイムアウトを付ける。
    Anthropic が遅延/不達でも bot の room join を遅らせない (失敗は無視)。
    """
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        await asyncio.wait_for(
            client.messages.create(
                model=LLM_MODEL, max_tokens=1, messages=[{"role": "user", "content": "hi"}]
            ),
            timeout=5.0,
        )
        logger.info("LLM prewarmed ({})", LLM_MODEL)
    except Exception as e:  # noqa: BLE001
        logger.warning("prewarm skipped: {}", e)


async def run_bot(transport: BaseTransport) -> None:
    logger.info("Starting {} bot", AGENT_NAME)
    if PREWARM:
        # 背景で温める (起動を block しない)。GC 防止に set へ退避。
        task = asyncio.create_task(_prewarm_llm())
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)

    live_kwargs: dict = {
        "language": STT_LANGUAGE,
        "model": STT_MODEL,
        "interim_results": True,
        "smart_format": True,
    }
    keyterms = [k.strip() for k in STT_KEYTERMS.split(",") if k.strip()]
    if keyterms:
        # nova-3 は keyterm、nova-2 は keywords で用語ブースト (テーマの固有名詞の誤認識を抑制)
        live_kwargs["keyterm" if STT_MODEL.startswith("nova-3") else "keywords"] = keyterms
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(**live_kwargs),
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
    processors.append(tts)
    if RECORD_RAW:
        processors.append(RawTTSTap(RECORD_RAW))  # 診断: TTS 直後で素の出力をタップ
    processors += [transport.output(), assistant_aggregator]

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
                    f.write(bot_audio)  # on_track_audio_data は既に bytes
        else:

            @audiobuffer.event_handler("on_audio_data")
            async def _on_audio_data(
                buf: object, audio: bytes, sample_rate: int, num_channels: int
            ) -> None:
                # crash-safe: コールバック毎に open/append/close (SIGINT で落ちても直前まで残る)
                with open(RECORD_PATH, "ab") as f:
                    f.write(audio)  # on_audio_data は既に bytes

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
        # codex P1: 「任意の参加者離脱」の alias。残り参加者が居る間は終了しない
        # (聴衆がタブを閉じても bot-to-bot 会話/録音は継続)。あい単独ブラウザでは相手=ユーザー
        # 離脱で残り 0 → 終了 (孤児セッション/課金防止)。participants() 取得失敗時は安全側で終了。
        remaining = 0
        try:
            left_id = client.get("id") if isinstance(client, dict) else None
            for k, v in (transport.participants() or {}).items():
                if k == "local" or (isinstance(v, dict) and v.get("local")):
                    continue
                if left_id and k == left_id:
                    continue
                remaining += 1
        except Exception as e:  # noqa: BLE001 (取得失敗は終了に倒す)
            logger.warning("participants() 取得失敗 ({}); 安全側で終了", e)
        if remaining > 0:
            logger.info("participant left, {} remain; keep session", remaining)
            return
        logger.info("no participants remain, ending")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


# ===== duo ブロードキャスター: AI同士の会話をライブで Daily に配信 (聴衆は聴くだけ) =====
# STT/入力なし。会話をテキストで生成→REST TTS でフル品質 PCM→transport 出力へ流す
# (record_text のライブ配信版)。STT 誤認識・継ぎ目なしの高整合性をそのままライブに。
DUO_VOICE_B = os.getenv("DUO_VOICE_B") or "GxhGYQesaQaYKePCZDEC"  # 相手「ゆう」の声
DUO_PERSONA_B = os.getenv("DUO_PERSONA_B") or (
    "あなたは好奇心旺盛で気さくな日本語話者「ゆう」です。砕けた短い日本語で、相手の話に反応しつつ"
    "自分の話もします。挨拶は最初の一度だけ。意味不明な入力は勝手に解釈せず流し、口調は一貫させ、"
    "毎ターン質問で締めず、相手が言っていない内容を勝手に要約しません。1〜2文で簡潔に。"
)
DUO_TURNS = int(os.getenv("DUO_TURNS", "0") or "0") or 14
DUO_GAP_S = 0.4


async def _duo_llm(system: str, user: str, key: str, model: str) -> str:
    body = json.dumps(
        {"model": model, "max_tokens": 220, "system": system,
         "messages": [{"role": "user", "content": user}]}
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST",
    )
    resp = await asyncio.to_thread(lambda: json.loads(urllib.request.urlopen(req, timeout=60).read()))
    return "".join(b.get("text", "") for b in resp.get("content", [])).strip()


async def _duo_tts(text: str, voice: str, key: str) -> bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_24000"
    body = json.dumps(
        {"text": text, "model_id": TTS_MODEL,
         "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "use_speaker_boost": True}}
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"xi-api-key": key, "content-type": "application/json"}, method="POST",
    )
    return await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=90).read())


class BroadcasterSource(FrameProcessor):
    """AI同士の会話を生成し PCM を出力に流すソース。聴衆 join で start()。"""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self._theme = theme
        self._started = False
        self._tasks: set = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        t = asyncio.create_task(self._run())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _system(self, persona: str, opener: bool) -> str:
        if not self._theme:
            return persona
        if opener:
            return persona + f"\n今日の話題は「{self._theme}」。自分の意見を述べてから相手に振って始める。"
        return persona + f"\n相手が「{self._theme}」を切り出すので、答えて自分の体験も話し話題を続ける。"

    async def _produce(self, q: asyncio.Queue) -> None:
        akey, ekey = os.getenv("ANTHROPIC_API_KEY"), os.getenv("ELEVENLABS_API_KEY")
        spk = [("あい", PERSONA, os.getenv("ELEVENLABS_VOICE_ID")),
               ("ゆう", DUO_PERSONA_B, DUO_VOICE_B)]
        history: list = []
        try:
            for turn in range(DUO_TURNS):
                name, persona, voice = spk[turn % 2]
                system = self._system(persona, turn % 2 == 0)
                if history:
                    tr = "\n".join(f"{w}: {t}" for w, t in history)
                    user = (f"これまでの会話:\n{tr}\n\nあなたは「{name}」。次のあなたの発話だけを"
                            "1〜2文で返す(名前ラベル無し)。")
                elif self._theme:
                    user = f"「{self._theme}」について自分の意見を述べてから相手に振って会話を始めて。"
                else:
                    user = "自然に挨拶して会話を始めて。"
                text = await _duo_llm(system, user, akey, LLM_MODEL)
                history.append((name, text))
                logger.info("[duo] {}: {}", name, text)
                await q.put(await _duo_tts(text, voice, ekey))
        except Exception as e:  # noqa: BLE001
            logger.warning("[duo] produce 終了: {}", e)
        await q.put(None)

    async def _run(self) -> None:
        q: asyncio.Queue = asyncio.Queue(maxsize=3)
        p = asyncio.create_task(self._produce(q))
        self._tasks.add(p)
        chunk = (24000 // 100) * 2 * 4  # ~40ms (mono 16-bit)
        while True:
            pcm = await q.get()
            if pcm is None:
                break
            for i in range(0, len(pcm), chunk):
                await self.push_frame(
                    OutputAudioRawFrame(pcm[i : i + chunk], 24000, 1), FrameDirection.DOWNSTREAM
                )
            await asyncio.sleep(len(pcm) / 2 / 24000 + DUO_GAP_S)  # 再生尺 + 間


async def run_broadcaster(transport: BaseTransport, theme: str) -> None:
    logger.info("Starting duo broadcaster (theme={})", theme or "なし")
    source = BroadcasterSource(theme)
    worker = PipelineWorker(
        Pipeline([source, transport.output()]),
        params=PipelineParams(enable_metrics=False),
        observers=[],
    )

    @transport.event_handler("on_first_participant_joined")
    async def on_join(transport: BaseTransport, participant: object) -> None:
        logger.info("audience joined → start broadcast")
        source.start()

    @transport.event_handler("on_client_disconnected")
    async def on_dc(transport: BaseTransport, client: object) -> None:
        remaining = 0
        try:
            left = client.get("id") if isinstance(client, dict) else None
            for k, v in (transport.participants() or {}).items():
                if k == "local" or (isinstance(v, dict) and v.get("local")) or k == left:
                    continue
                remaining += 1
        except Exception:  # noqa: BLE001
            remaining = 0
        if remaining == 0:
            logger.info("no audience remains, ending broadcast")
            await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    """Pipecat Cloud / runner エントリポイント。"""
    if not os.getenv("ELEVENLABS_VOICE_ID"):
        raise RuntimeError("ELEVENLABS_VOICE_ID (声優 voice_id) が未設定です")

    body = getattr(runner_args, "body", None) or {}
    duo = str(body.get("DUO", os.getenv("DUO", "0"))).lower() not in ("0", "", "false", "none")
    theme = str(body.get("THEME") or os.getenv("DUO_THEME") or "")

    match runner_args:
        case DailyRunnerArguments():
            if duo:  # AI同士ライブ配信 (出力専用、聴衆が聴く)
                transport = DailyTransport(
                    runner_args.room_url, runner_args.token, "AI同士",
                    params=DailyParams(audio_in_enabled=False, audio_out_enabled=True),
                )
                await run_broadcaster(transport, theme)
                return
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
