"""VOICEVOX ENGINE エンジン (ローカル HTTP)。

低レベル REST 呼び出し (audio_query → synthesis, urllib・追加 python 依存なし) は
aiconv.adapters._engines.voicevox_engine に委譲する (core アダプタ tts_voicevox と共有 —
二重実装の禁止)。本モジュールはベンチ計測のみを担う。ENGINE は LGPL だが HTTP の
プロセス分離で使うため本リポのコードに伝播しない。tamayori-tts の VOICEVOX ONNX 運用と互換。
アクセント編集 (accent_phrases 書き換え + mora_data) は core アダプタ側で実装済み —
本ハーネスでは既定クエリのまま合成する。

非ストリーミング API のため TTFA = 全合成時間として記録する。
"""

from __future__ import annotations

import time

from metrics import pcm_duration_s

from aiconv.adapters._engines import EngineUnavailableError as CoreEngineUnavailableError
from aiconv.adapters._engines.voicevox_engine import VoicevoxClient, parse_wav

from .base import Engine, EngineUnavailableError, SynthResult


class VoicevoxEngine(Engine):
    name = "voicevox"

    def __init__(self, *, base_url: str | None = None, speaker: int | None = None) -> None:
        self._client = VoicevoxClient(base_url=base_url, speaker=speaker)

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def speaker(self) -> int:
        return self._client.speaker

    def check(self) -> str | None:
        return self._client.check()

    def synthesize(self, text: str) -> SynthResult:
        t0 = time.perf_counter()
        try:
            query = self._client.audio_query(text)
            wav_bytes = self._client.synthesis(query)
        except CoreEngineUnavailableError as e:
            raise EngineUnavailableError(str(e)) from e
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        pcm, sample_rate = parse_wav(wav_bytes)
        return SynthResult(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=pcm_duration_s(pcm, sample_rate),
            ttfa_ms=elapsed_ms,  # 非ストリーミング: 最初の音=全合成完了
            elapsed_ms=elapsed_ms,
            streaming=False,
            notes=f"speaker={self.speaker} query_sr={query.get('outputSamplingRate')}",
        )
