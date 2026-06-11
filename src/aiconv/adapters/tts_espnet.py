"""ESPnet2 VITS TTSProvider アダプタ (Apache-2.0 — 設計 japanese-tts-optimization §3 本命)。

ESPnet をプロセス内ロードして合成する (HTTP/サイドカー不要, 設計 §5-L6)。合成前に必ず
L0 正規化 (aiconv.frontend.normalize) を通す。アクセント (L1) は既定モデルの G2P
(pyopenjtalk_accent_with_pause) がモデル内部で付与するため、アダプタからの明示注入は不要。
出力波形 (ネイティブ 22.05kHz 等) は 16kHz mono PCM (_PCM_16K) へリサンプルして
AudioFrame で返す。

★アダプタ責務 (tts_elevenlabs と同じ): VoiceLicense で許諾範囲を強制し、合成テキストを
  AuditSink に必ず記録する。許諾外テキストは合成しない (声優人格権の構造的保護)。

espnet スタックは遅延 import + lazy load (import / 生成時にモデル DL・GPU 確保をしない)。
未導入でも本モジュールの import と生成は成功し、capabilities.available=False を示しつつ、
synthesize 呼び出し時に EngineUnavailableError (導入案内) を送出する。
Text2Speech は一括生成のため、interrupt の粒度はチャンク単位 (推論中の中断は次チャンクで効く)。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..core.events import AudioFormat, AudioFrame
from ..core.ports import AuditSink, Capability, VoiceLicense
from ..frontend import normalize
from ._engines.espnet_engine import EspnetSynthesizer, espnet_available
from ._engines.resample import resample_pcm16

_PCM_16K = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)


class EspnetTTS:
    def __init__(
        self,
        *,
        model_tag: str | None = None,
        device: str | None = None,
        voice_id: str | None = None,
        license: VoiceLicense | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self._engine = EspnetSynthesizer(model_tag=model_tag, device=device)
        self.voice_id = voice_id or f"espnet:{self._engine.model_tag}"
        self.license = license
        self.audit = audit
        self._interrupted = False
        reason = espnet_available()
        self.capabilities = Capability(
            supports_interrupt=True,
            languages=("ja",),
            notes=(
                f"ESPnet2 VITS in-process; model={self._engine.model_tag}; "
                f"device={self._engine.device}" + (f"; 利用不可: {reason}" if reason else "")
            ),
            available=reason is None,
        )

    def _guard(self, text: str) -> bool:
        """許諾範囲を判定し、必ず監査ログに記録する (対象は実際にエンジンへ渡すテキスト)。"""
        allowed = self.license.permits(text) if self.license else True
        if self.audit is not None:
            self.audit.record(voice_id=self.voice_id, text=text, allowed=allowed)
        return allowed

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioFrame]:
        seq = 0
        async for chunk in text_chunks:
            if self._interrupted:
                break
            text = normalize(chunk)  # L0 (読み崩れ対策)。L1 はモデル内 G2P が担う
            if not text or not self._guard(text):
                continue
            # ブロッキング推論はスレッドへ逃がし、イベントループを止めない
            pcm, rate = await asyncio.to_thread(self._engine.synthesize, text)
            if self._interrupted:
                break
            yield AudioFrame(
                data=resample_pcm16(pcm, rate, _PCM_16K.sample_rate),
                ts_ms=0.0,
                seq=seq,
                fmt=_PCM_16K,
            )
            seq += 1

    async def interrupt(self) -> None:
        self._interrupted = True
