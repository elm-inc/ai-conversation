"""キャラクター人格と音声/モデル設定 (環境変数で上書き可)。

声優音源 (権利クリア) の voice_id を ELEVENLABS_VOICE_ID で渡す。
人格は一貫した会話人格 (設計 §人格)。Phase 2 で構造化ペルソナ仕様へ拡張する。
"""

from __future__ import annotations

import os

LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
TTS_MODEL = os.environ.get("TTS_MODEL", "eleven_flash_v2_5")
STT_MODEL = os.environ.get("STT_MODEL", "nova-2")
STT_LANGUAGE = os.environ.get("STT_LANGUAGE", "ja")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")

# 一貫した会話人格 (口調・価値観・NG)。音声で読み上げる前提で、絵文字や記号は避ける。
PERSONA = os.environ.get(
    "PERSONA_PROMPT",
    """あなたは親しみやすい日本語の話し相手「あい」です。

人格と話し方:
- 砕けた自然な日本語で、短めに話す。長い説明より相づちと問い返しを優先。
- 相手の話を否定せず、まず受け止めてから返す。
- 知ったかぶりをしない。分からないことは素直に「分からない」と言う。

音声出力の制約:
- 読み上げられるので、絵文字・顔文字・記号の羅列・箇条書き・URL は出さない。
- 1〜2文で簡潔に。間を大事にし、相手に話す余白を残す。""",
)
