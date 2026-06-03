"""ElevenLabs TTSProvider アダプタ — スケルトン。

声優音源 (権利クリア) の voice_id を固定し、Flash v2.5 でストリーミング合成する想定。
★アダプタ責務 (設計レビュー反映): VoiceLicense で許諾範囲を強制し、合成テキストを
  AuditSink に必ず記録する。許諾外テキストは合成しない。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from ..core.events import AudioFrame
from ..core.ports import AuditSink, Capability, VoiceLicense


class ElevenLabsTTS:
    capabilities = Capability(
        supports_interrupt=True,
        flush_latency_ms=None,
        languages=("ja", "en"),
        notes="ElevenLabs Flash v2.5; 声優ボイス固定",
    )

    def __init__(
        self,
        *,
        voice_id: str,
        license: VoiceLicense | None = None,
        audit: AuditSink | None = None,
        api_key: str | None = None,
    ) -> None:
        self.voice_id = voice_id
        self.license = license
        self.audit = audit
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")

    def _guard(self, text: str) -> bool:
        allowed = self.license.permits(text) if self.license else True
        if self.audit is not None:
            self.audit.record(voice_id=self.voice_id, text=text, allowed=allowed)
        return allowed

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioFrame]:
        raise NotImplementedError(
            "Phase 0 スケルトン: ElevenLabs streaming を実装。_guard() で許諾/監査を通す (AIC-1)"
        )
        yield

    async def interrupt(self) -> None:
        raise NotImplementedError("Phase 0 スケルトン (AIC-1 次段)")
