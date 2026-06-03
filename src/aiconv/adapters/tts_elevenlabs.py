"""ElevenLabs TTSProvider アダプタ。

声優音源 (権利クリア) の voice_id を固定し、Flash v2.5 でストリーミング合成する。
出力は raw PCM 16kHz (output_format=pcm_16000) に正規化して AudioFrame で返す。

★アダプタ責務 (設計レビュー反映): VoiceLicense で許諾範囲を強制し、合成テキストを
  AuditSink に必ず記録する。許諾外テキストは合成しない (声優人格権の構造的保護)。

SDK は遅延 import。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from ..core.events import AudioFormat, AudioFrame
from ..core.ports import AuditSink, Capability, VoiceLicense

_PCM_16K = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)


class ElevenLabsTTS:
    capabilities = Capability(
        supports_interrupt=True,
        languages=("ja", "en"),
        notes="ElevenLabs Flash v2.5; 声優ボイス固定; pcm_16000",
    )

    def __init__(
        self,
        *,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        license: VoiceLicense | None = None,
        audit: AuditSink | None = None,
        api_key: str | None = None,
    ) -> None:
        self.voice_id = voice_id
        self.model_id = model_id
        self.license = license
        self.audit = audit
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        self._interrupted = False
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from elevenlabs.client import AsyncElevenLabs

            self._client = AsyncElevenLabs(api_key=self.api_key)
        return self._client

    def _guard(self, text: str) -> bool:
        """許諾範囲を判定し、必ず監査ログに記録する。"""
        allowed = self.license.permits(text) if self.license else True
        if self.audit is not None:
            self.audit.record(voice_id=self.voice_id, text=text, allowed=allowed)
        return allowed

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioFrame]:
        client = self._ensure_client()
        seq = 0
        async for chunk in text_chunks:
            if self._interrupted:
                break
            if not chunk.strip() or not self._guard(chunk):
                continue
            audio_stream = client.text_to_speech.stream(
                voice_id=self.voice_id,
                model_id=self.model_id,
                text=chunk,
                output_format="pcm_16000",
            )
            async for audio in audio_stream:
                if self._interrupted:
                    break
                yield AudioFrame(data=bytes(audio), ts_ms=0.0, seq=seq, fmt=_PCM_16K)
                seq += 1

    async def interrupt(self) -> None:
        self._interrupted = True
