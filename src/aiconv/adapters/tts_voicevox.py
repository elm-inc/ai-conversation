"""VOICEVOX ENGINE TTSProvider アダプタ (LGPL を HTTP プロセス分離で利用 — 設計 §3 並列採用)。

ローカル VOICEVOX ENGINE (既定 http://127.0.0.1:50021) へ audio_query → synthesis。
合成前に必ず L0 正規化 (aiconv.frontend.normalize) を通す。さらに pyopenjtalk
(frontend extra) が導入済みなら L1 のアクセント核を audio_query の accent_phrases に注入し、
mora_data でピッチを再計算してから合成する (graceful: L1 と ENGINE の句構造が完全一致した
場合のみ書き換え。失敗・未導入時は ENGINE 既定アクセントのまま合成し、落とさない)。
出力 WAV (既定 24kHz) は 16kHz mono PCM (_PCM_16K) へリサンプルして AudioFrame で返す。

★アダプタ責務 (tts_elevenlabs と同じ): VoiceLicense で許諾範囲を強制し、合成テキストを
  AuditSink に必ず記録する。許諾外テキストは合成しない。

ENGINE 未起動でも import / 生成は成功し、synthesize 呼び出し時に EngineUnavailableError
(起動案内) を送出する。非ストリーミング API のため interrupt の粒度はチャンク単位。
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..core.events import AudioFormat, AudioFrame
from ..core.ports import AuditSink, Capability, VoiceLicense
from ..frontend import AccentPhrase, frontend_available, normalize, predict_accent
from ._engines import EngineUnavailableError
from ._engines.resample import resample_pcm16
from ._engines.voicevox_engine import VoicevoxClient, parse_wav

_PCM_16K = AudioFormat(sample_rate=16_000, channels=1, sample_width=2)


def inject_accent(
    accent_phrases: list[dict[str, Any]], phrases: Sequence[AccentPhrase]
) -> list[dict[str, Any]] | None:
    """L1 のアクセント核を VOICEVOX accent_phrases へ反映した deep copy を返す。

    句数と各句のモーラ数が完全一致した場合のみ注入する (保守的 — 不一致なら None を返し、
    呼び出し側は ENGINE 既定アクセントのまま合成する)。VOICEVOX は平板型を
    「核位置 = モーラ数 (句末核)」で表すため、L1 の 0 (平板) は句末核へ変換する。
    注入後は ENGINE の mora_data でピッチ再計算が必要 (synthesis は moras のピッチ値を使う)。
    """
    if len(accent_phrases) != len(phrases):
        return None
    for vp, p in zip(accent_phrases, phrases, strict=True):
        moras = vp.get("moras")
        if not isinstance(moras, list) or len(moras) != p.mora_count:
            return None
    out = copy.deepcopy(accent_phrases)
    for vp, p in zip(out, phrases, strict=True):
        vp["accent"] = p.accent if p.accent > 0 else len(vp["moras"])
    return out


class VoicevoxTTS:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        speaker: int | None = None,
        voice_id: str | None = None,
        license: VoiceLicense | None = None,
        audit: AuditSink | None = None,
        use_accent: bool = True,
    ) -> None:
        self._client = VoicevoxClient(base_url=base_url, speaker=speaker)
        self.voice_id = voice_id or f"voicevox:{self._client.speaker}"
        self.license = license
        self.audit = audit
        self.use_accent = use_accent
        self._interrupted = False
        self.capabilities = Capability(
            supports_interrupt=True,
            languages=("ja",),
            notes=(
                f"VOICEVOX ENGINE (LGPL, HTTP プロセス分離); url={self._client.base_url}; "
                f"speaker={self._client.speaker}; "
                f"L1 アクセント注入={'on' if use_accent else 'off'}; "
                "未起動は synthesize 時に検出"
            ),
        )

    def _guard(self, text: str) -> bool:
        """許諾範囲を判定し、必ず監査ログに記録する (対象は実際にエンジンへ渡すテキスト)。"""
        allowed = self.license.permits(text) if self.license else True
        if self.audit is not None:
            self.audit.record(voice_id=self.voice_id, text=text, allowed=allowed)
        return allowed

    async def _refine_accent(self, text: str, query: dict[str, Any]) -> dict[str, Any]:
        """L1 アクセントを audio_query に注入する (任意機能 — 失敗時は元のクエリで続行)。"""
        if not self.use_accent or frontend_available() is not None:
            return query
        base = query.get("accent_phrases")
        if not isinstance(base, list):
            return query
        try:
            phrases = predict_accent(text)
            injected = inject_accent(base, phrases)
            if injected is None:
                return query
            refreshed = await asyncio.to_thread(self._client.mora_data, injected)
            return {**query, "accent_phrases": refreshed}
        except EngineUnavailableError:
            raise  # 接続断は隠さない (上位で起動案内として見せる)
        except Exception:  # noqa: BLE001 — アクセント注入は加点機能。失敗しても合成は続行
            return query

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioFrame]:
        seq = 0
        async for chunk in text_chunks:
            if self._interrupted:
                break
            text = normalize(chunk)  # L0 (読み崩れ対策)
            if not text or not self._guard(text):
                continue
            # ブロッキング HTTP はスレッドへ逃がし、イベントループを止めない
            query = await asyncio.to_thread(self._client.audio_query, text)
            query = await self._refine_accent(text, query)
            if self._interrupted:
                break
            wav = await asyncio.to_thread(self._client.synthesis, query)
            if self._interrupted:
                break
            pcm, rate = parse_wav(wav)
            yield AudioFrame(
                data=resample_pcm16(pcm, rate, _PCM_16K.sample_rate),
                ts_ms=0.0,
                seq=seq,
                fmt=_PCM_16K,
            )
            seq += 1

    async def interrupt(self) -> None:
        self._interrupted = True
