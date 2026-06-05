"""run_live_duo をローカル検証する。Daily room を REST で作り、あい+ゆう を同一プロセスの
同室に2体生やして実音声で会話させる。room URL を表示するのでブラウザで開けば聴ける。
logs で あい/ゆう が互いの発話を STT→LLM→TTS しているか (相互理解) を確認する。

    uv run python run_live_local.py --theme "おすすめの映画" --seconds 90
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import bot


def _tok(name: str) -> str:
    p = Path(f"~/.{name}_token").expanduser()
    return p.read_text().strip() if p.is_file() else ""


def _create_room(api_key: str, exp_min: int = 30) -> str:
    body = json.dumps(
        {"properties": {"exp": int(time.time()) + exp_min * 60, "eject_at_room_exp": True}}
    ).encode()
    req = urllib.request.Request(
        "https://api.daily.co/v1/rooms", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["url"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="")
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--voice-a", default="lhTvHflPVOqgSWyuWQry", help="あいの voice_id")
    args = ap.parse_args()

    for t in ("daily", "deepgram", "anthropic", "elevenlabs"):
        if not _tok(t):
            raise SystemExit(f"~/.{t}_token が無い")
    os.environ.update(
        {
            "DAILY_API_KEY": _tok("daily"),
            "DEEPGRAM_API_KEY": _tok("deepgram"),
            "ANTHROPIC_API_KEY": _tok("anthropic"),
            "ELEVENLABS_API_KEY": _tok("elevenlabs"),
            "ELEVENLABS_VOICE_ID": args.voice_a,
        }
    )
    # 実機 cloud と同じく haiku + 安全打ち切りは --seconds に合わせる (module global を実行時上書き)。
    bot.LLM_MODEL = os.getenv("ANTHROPIC_MODEL") or "claude-haiku-4-5"
    bot.LIVE_MAX_S = args.seconds

    daily_key = _tok("daily")
    room = _create_room(daily_key)
    ai_token = bot._mint_daily_token(room, daily_key) or ""
    print(f"\n[room] 聴衆はここで聴けます (ブラウザで開く):\n  {room}\n")
    print(f"[live] あい+ゆう を同室起動 (~{args.seconds}s, theme={args.theme or 'なし'})\n")
    await bot.run_live_duo(room, ai_token, args.theme)
    print("\n[done] LIVE duo 終了")


if __name__ == "__main__":
    asyncio.run(main())
