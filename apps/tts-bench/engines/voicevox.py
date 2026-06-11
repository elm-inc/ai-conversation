"""VOICEVOX ENGINE エンジン (ローカル HTTP)。

audio_query → synthesis の 2 段 REST (record_text.py と同じ urllib スタイル、追加 python
依存なし)。ENGINE は LGPL だが HTTP のプロセス分離で使うため本リポのコードに伝播しない。
tamayori-tts の VOICEVOX ONNX 運用と互換。アクセント編集 (audio_query の accent_phrases
書き換え) も同 API で可能 — 本ハーネスでは既定クエリのまま合成する。

非ストリーミング API のため TTFA = 全合成時間として記録する。
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

from metrics import pcm_duration_s

from .base import Engine, EngineUnavailableError, SynthResult

_DEFAULT_URL = "http://127.0.0.1:50021"
_DEFAULT_SPEAKER = 3  # ずんだもん ノーマル (起動中の ENGINE の /speakers で確認・変更可)


def _parse_wav(data: bytes) -> tuple[bytes, int]:
    """WAV → (16-bit mono PCM, sample_rate)。想定外フォーマットは明確に落とす。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise RuntimeError(
                f"想定外の WAV 形式: {w.getsampwidth() * 8}bit {w.getnchannels()}ch "
                "(audio_query の outputStereo=false / 16bit を想定)"
            )
        return w.readframes(w.getnframes()), w.getframerate()


class VoicevoxEngine(Engine):
    name = "voicevox"

    def __init__(self, *, base_url: str | None = None, speaker: int | None = None) -> None:
        self.base_url = (base_url or os.environ.get("VOICEVOX_URL", _DEFAULT_URL)).rstrip("/")
        self.speaker = (
            speaker
            if speaker is not None
            else int(os.environ.get("VOICEVOX_SPEAKER", str(_DEFAULT_SPEAKER)))
        )

    def check(self) -> str | None:
        try:
            with urllib.request.urlopen(f"{self.base_url}/version", timeout=3) as r:
                r.read()
            return None
        except Exception as e:  # noqa: BLE001 — 接続不可は「未起動」として案内する
            return (
                f"VOICEVOX ENGINE に接続できない ({self.base_url}): {e} — "
                "README の手順で ENGINE を起動してから再実行"
            )

    def synthesize(self, text: str) -> SynthResult:
        t0 = time.perf_counter()
        try:
            q = urllib.parse.urlencode({"text": text, "speaker": self.speaker})
            req = urllib.request.Request(f"{self.base_url}/audio_query?{q}", method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                query: bytes = r.read()
            req2 = urllib.request.Request(
                f"{self.base_url}/synthesis?speaker={self.speaker}",
                data=query,
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req2, timeout=120) as r:
                wav_bytes: bytes = r.read()
        except urllib.error.HTTPError as e:  # URLError のサブクラスなので先に捕捉
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"VOICEVOX API エラー {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise EngineUnavailableError(f"{self.name}: ENGINE に接続できない: {e}") from e
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        pcm, sample_rate = _parse_wav(wav_bytes)
        sr_q = json.loads(query).get("outputSamplingRate")
        return SynthResult(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=pcm_duration_s(pcm, sample_rate),
            ttfa_ms=elapsed_ms,  # 非ストリーミング: 最初の音=全合成完了
            elapsed_ms=elapsed_ms,
            streaming=False,
            notes=f"speaker={self.speaker} query_sr={sr_q}",
        )
