"""実プロバイダで cascaded ループを回し、ベースライン遅延を計測する。

実 API キー (環境変数) と入力 WAV、声優 voice_id が必要。

    export ANTHROPIC_API_KEY=... DEEPGRAM_API_KEY=... ELEVENLABS_API_KEY=...
    uv run --extra providers python -m aiconv.poc.run_real \
        --in input.wav --out reply.wav --voice <ELEVENLABS_VOICE_ID>

★安全: 声優音源を使うため、VoiceLicense (許諾範囲) と AuditSink (監査ログ) を必ず渡す。
"""

from __future__ import annotations

import argparse
import asyncio
import os

from ..adapters.llm_claude import ClaudeLLM
from ..adapters.stt_deepgram import DeepgramSTT
from ..adapters.transport_wav import WavFileTransport
from ..adapters.tts_elevenlabs import ElevenLabsTTS
from ..adapters.turn import SilenceTurnDetector
from ..core.orchestrator import ConversationOrchestrator, OrchestratorConfig
from ..core.ports import VoiceLicense

_REQUIRED_KEYS = ("ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY")


class StdoutAudit:
    def record(self, *, voice_id: str, text: str, allowed: bool) -> None:
        flag = "ALLOW" if allowed else "DENY"
        print(f"[audit:{flag}] voice={voice_id} text={text!r}")


def missing_keys() -> list[str]:
    return [k for k in _REQUIRED_KEYS if not os.environ.get(k)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", default="reply.wav")
    parser.add_argument("--voice", dest="voice_id", required=True)
    parser.add_argument("--system", dest="system", default="あなたは親しみやすい相棒。")
    args = parser.parse_args()

    if missing := missing_keys():
        raise SystemExit(f"必要な API キーが未設定: {', '.join(missing)}")

    transport = WavFileTransport(args.in_path, args.out_path)
    orch = ConversationOrchestrator(
        stt=DeepgramSTT(),
        llm=ClaudeLLM(),
        tts=ElevenLabsTTS(
            voice_id=args.voice_id,
            # PoC では全許諾。本番は声優契約に基づく allow 関数に差し替える。
            license=VoiceLicense(voice_id=args.voice_id, allow=lambda _text: True),
            audit=StdoutAudit(),
        ),
        turn_detector=SilenceTurnDetector(),
        transport=transport,
        config=OrchestratorConfig(system_prompt=args.system),
    )
    reply = await orch.run_turn()
    await transport.stop_playback()
    print(f"reply: {reply}")
    print(f"wrote: {args.out_path}")
    print(orch.metrics.report())


if __name__ == "__main__":
    asyncio.run(main())
