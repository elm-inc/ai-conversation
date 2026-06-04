"""会話テスター Director (AIC-7 Phase B)。

あい (Pipecat Cloud) を Daily ルームに起動し、interlocutor「ゆう」をローカルで同じルームに
join させて AI 同士の会話を回す。ルーム URL を表示するので、ブラウザで開けば聴衆として聴ける
(将来 content の原型)。bot.py (apps/voice-agent) を role パラメータで再利用する。

    uv run python director.py --seconds 120

前提: ~/.{deepgram,anthropic,elevenlabs,daily}_token、あいは pcc にデプロイ済み。
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

VOICE_AGENT = Path(__file__).resolve().parents[1] / "voice-agent"

YUU_PERSONA = (
    "あなたは好奇心旺盛で気さくな日本語話者「ゆう」です。"
    "会話相手『あい』と自然に雑談します。砕けた短い日本語で、相手の話に反応しつつ自分の話もします。"
    "読み上げ前提なので絵文字・記号は出さず、1〜2文で簡潔に、自然なキャッチボールを続けます。"
)
YUU_SCENARIO = (
    "目標: ①軽い挨拶と近況 ②週末の予定の話題を振る ③途中で自然に天気の話へ移る "
    "④相手の答えに共感や軽い質問を返す。不自然に終わらせず会話を続ける。"
)
# NOTE: ユーザー提供の Raa94hHxcH2itBN60mKp は ElevenLabs に存在せず TTS 失敗したため、
# 当面あいと同じ動く voice で会話成立を検証。有効な別 voice_id を貰い次第差し替える。
YUU_VOICE_ID = "c2XJrw7TvNGtOc6r0ijG"


def _tok(name: str) -> str:
    p = Path(f"~/.{name}_token").expanduser()
    return p.read_text().strip() if p.is_file() else ""


def start_ai_room() -> tuple[str, str]:
    """あいを起動しルーム URL を返す。(join用フルURL, base room URL)。"""
    res = subprocess.run(
        ["pipecat", "cloud", "agent", "start", "ai-conversation-voice", "--use-daily", "--force"],
        capture_output=True,
        text=True,
        timeout=200,
    )
    joined = re.sub(r"\s+", "", res.stdout + res.stderr)
    m = re.search(r"(https://[^\s]*?daily\.co/[A-Za-z0-9]+\?t=[A-Za-z0-9._-]+)", joined)
    if not m:
        print(res.stdout, res.stderr, file=sys.stderr)
        raise SystemExit("room URL を取得できませんでした")
    full = m.group(1)
    base = full.split("?", 1)[0]  # DAILY_ROOM_URL には token 無しの base を渡す
    return full, base


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    args = ap.parse_args()

    for t in ("deepgram", "anthropic", "elevenlabs", "daily"):
        if not _tok(t):
            raise SystemExit(f"~/.{t}_token が無い")

    full_url, base_url = start_ai_room()
    print(f"\n[room] 聴衆はここで聴けます:\n  {full_url}\n")

    env = os.environ.copy()
    env.update(
        {
            "DEEPGRAM_API_KEY": _tok("deepgram"),
            "ANTHROPIC_API_KEY": _tok("anthropic"),
            "ELEVENLABS_API_KEY": _tok("elevenlabs"),
            "DAILY_API_KEY": _tok("daily"),
            "DAILY_ROOM_URL": base_url,
            "AGENT_NAME": "ゆう",
            "PERSONA_PROMPT": YUU_PERSONA,
            "SCENARIO": YUU_SCENARIO,
            "KICKOFF_PROMPT": "シナリオに沿って、相手に自然に話しかけて会話を始めて。",
            "ELEVENLABS_VOICE_ID": YUU_VOICE_ID,
            "KICKOFF": "0",  # RTVI 口火は使わない
            "KICKOFF_ON_JOIN": "1",  # あいが居る部屋に入ったら口火を切る
            "STT_LANGUAGE": "ja",
            "STT_MODEL": "nova-2",
            "TTS_MODEL": "eleven_multilingual_v2",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        }
    )

    print(f"[interlocutor] ゆう をローカル起動 (~{args.seconds}s)...")
    proc = subprocess.Popen(
        ["uv", "run", "run_interlocutor.py"],
        cwd=str(VOICE_AGENT),
        env=env,
    )
    try:
        time.sleep(args.seconds)
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("[done] interlocutor 停止")


if __name__ == "__main__":
    main()
