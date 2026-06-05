"""会話プリセット (キャラクター/言語) の単一ソース。

ペルソナ・声・シナリオ・言語の組をここに集約する。新しいキャラや言語を増やすときは
PRESETS にブロックを 1 つ足すだけ。director.py / record_conversation.py が import して使う
(実行スクリプト側に設定を散らさない — gemini レビュー指摘)。
"""

from __future__ import annotations

# ⚠ SR は別 app の bot.py `RECORD_SAMPLE_RATE` と必ず一致させること
# (raw PCM を書く側/wav 化する側で食い違うと録音が再生不能になる)。別 package のため import 不可。
SR = 24000  # bot track のサンプルレート

# --- 声 (ElevenLabs voice_id)。Library voice は「My Voices に追加」済みで websocket TTS 可 ---
AI_VOICE_ID = "lhTvHflPVOqgSWyuWQry"  # あい本番
YUU_VOICE_ID = "GxhGYQesaQaYKePCZDEC"  # ゆう

# --- 日本語ペルソナ ---
YUU_PERSONA = (
    "あなたは好奇心旺盛で気さくな日本語話者「ゆう」です。"
    "会話相手『あい』と自然に雑談します。砕けた短い日本語で、相手の話に反応しつつ自分の話もします。"
    "挨拶は最初の一度だけにし、会話の途中で挨拶を繰り返さず、話題の続きから自然に話します。"
    "読み上げ前提なので絵文字・記号は出さず、1〜2文で簡潔に、自然なキャッチボールを続けます。"
)
YUU_SCENARIO = (
    "目標: ①軽い挨拶と近況 ②週末の予定の話題を振る ③途中で自然に天気の話へ移る "
    "④相手の答えに共感や軽い質問を返す。不自然に終わらせず会話を続ける。"
)

# --- 英語ペルソナ ---
ALEX_PERSONA = (
    "You are Alex, a curious and easygoing English speaker having a casual chat with Sam. "
    "Speak in short, natural spoken English, one or two sentences at a time. React to what "
    "the other person says and share a little about yourself. Greet only once at the very "
    "start; do not repeat greetings later. Your words are read aloud, so no emojis, symbols, "
    "bullet points, or URLs."
)
SAM_PERSONA = (
    "You are Sam, a warm and talkative English speaker chatting with Alex. Speak in short, "
    "natural spoken English, one or two sentences at a time, reacting to Alex and adding your "
    "own thoughts. Greet only once at the start, then keep the conversation flowing from the "
    "topic. No emojis or symbols, since this is read aloud."
)
SAM_SCENARIO = (
    "Goal: 1) a light greeting and how things are going, 2) bring up weekend plans, 3) drift "
    "naturally into the weather, 4) respond with empathy and light follow-up questions. Keep "
    "the conversation going naturally and do not end it abruptly."
)

# テーマ (話題) 注入テンプレ。--theme 指定時に preset の language で選ぶ (両者の scenario と
# 口火役の kickoff を上書き)。新言語は 1 言語分足す (無ければ en にフォールバック)。
THEME_TEMPLATES: dict = {
    "ja": {
        "scenario": "今日の話題は「{theme}」です。この話題を中心に自然に雑談を続けてください。",
        "kickoff": "「{theme}」について相手に自然に話を振って会話を始めてください。",
    },
    "en": {
        "scenario": 'Today\'s topic is "{theme}". Keep the conversation centered on it.',
        "kickoff": 'Start by naturally bringing up the topic "{theme}".',
    },
}

# 各 preset = 言語 + STT/TTS/LLM + スピーカー2体。speaker[0] が口火 (kickoff)、[1] が応答役。
# persona=None なら bot.py の既定ペルソナ (= 日本語「あい」) を使う。
PRESETS: dict = {
    "ja": {
        "language": "ja",
        "stt_model": "nova-2",
        "tts_model": "eleven_multilingual_v2",
        "anthropic_model": "claude-haiku-4-5",
        "speakers": [
            {
                "name": "あい",
                "persona": None,  # bot.py 既定 (あい)
                "voice": AI_VOICE_ID,
                "scenario": None,
                "kickoff": True,
                "kickoff_prompt": "まず一言で挨拶して、相手に自然に話しかけて。",
            },
            {
                "name": "ゆう",
                "persona": YUU_PERSONA,
                "voice": YUU_VOICE_ID,
                "scenario": YUU_SCENARIO,
                "kickoff": False,
                "kickoff_prompt": "",
            },
        ],
    },
    "en": {
        "language": "en",
        "stt_model": "nova-2",
        "tts_model": "eleven_multilingual_v2",
        "anthropic_model": "claude-haiku-4-5",
        "speakers": [
            {
                "name": "Alex",
                "persona": ALEX_PERSONA,
                "voice": "0S5oIfi8zOZixuSj8K6n",
                "scenario": None,
                "kickoff": True,
                "kickoff_prompt": "Greet briefly and start a natural conversation.",
            },
            {
                "name": "Sam",
                "persona": SAM_PERSONA,
                "voice": "ZSNL4hPqCnqoMPaI4jGX",
                "scenario": SAM_SCENARIO,
                "kickoff": False,
                "kickoff_prompt": "",
            },
        ],
    },
}
