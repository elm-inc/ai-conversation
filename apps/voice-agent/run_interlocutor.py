"""interlocutor を既存 Daily ルーム (DAILY_ROOM_URL) に直接 join させ bot() を回すエントリ。

`bot.py --transport daily` は dev サーバを立てるだけでルームに直接 join しない。AIC-7 の
director から env (DAILY_ROOM_URL / DAILY_API_KEY / 役割 env) 経由で起動され、あいの居る
ルームにもう1体 (ゆう) を join させる。
"""

from __future__ import annotations

import asyncio

import aiohttp
from pipecat.runner.daily import configure
from pipecat.runner.types import DailyRunnerArguments

import bot


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        cfg = await configure(session)  # DAILY_ROOM_URL + DAILY_API_KEY から room+token
    await bot.bot(DailyRunnerArguments(room_url=cfg.room_url, token=cfg.token))


if __name__ == "__main__":
    asyncio.run(main())
