"""声優フィラークリップを ElevenLabs から一度だけ生成する (レイテンシ隠蔽用)。

    uv run python -m aiconv.poc.render_fillers --voice <ELEVENLABS_VOICE_ID> --out fillers/

生成した WAV を run_real に渡す:
    uv run python -m aiconv.poc.run_real --in in.wav --voice <ID> --fillers-dir fillers/
"""

from __future__ import annotations

import argparse
import asyncio
import os
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from ..adapters.tts_elevenlabs import ElevenLabsTTS
from .run_real import load_token_files

# 声優の口癖として自然な短いフィラー/相槌
_DEFAULT_PHRASES = ("うーん", "そうだね", "なるほど", "えーっと")


async def _emit(text: str) -> AsyncIterator[str]:
    yield text


async def render(voice_id: str, out_dir: str, phrases: tuple[str, ...]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tts = ElevenLabsTTS(voice_id=voice_id)
    for i, phrase in enumerate(phrases):
        pcm = b"".join([fr.data async for fr in tts.synthesize(_emit(phrase))])
        path = out / f"{i:02d}_{phrase}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16_000)
            w.writeframes(pcm)
        print(f"wrote {path} ({len(pcm) / 2 / 16000:.2f}s)")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", dest="voice_id", required=True)
    parser.add_argument("--out", dest="out_dir", default="fillers")
    args = parser.parse_args()

    load_token_files()
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY が未設定です")
    await render(args.voice_id, args.out_dir, _DEFAULT_PHRASES)


if __name__ == "__main__":
    asyncio.run(main())
