"""VOICEVOX ENGINE (ローカル HTTP) の低レベルクライアント。

audio_query → (任意で accent_phrases 編集 → mora_data) → synthesis の REST 呼び出しのみを
担う同期層 (urllib、追加 python 依存なし)。ENGINE は LGPL-3.0 だが HTTP のプロセス分離で
利用するため本リポのコードに伝播しない (設計 japanese-tts-optimization §3)。
async 化・AudioFrame 正規化・L0/L1 は core アダプタ (tts_aivis)、TTFA 計測は
apps/tts-bench/engines/voicevox.py の責務 (両者がこのモジュールを共有する)。
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import wave
from typing import Any

from . import EngineUnavailableError

DEFAULT_URL = "http://127.0.0.1:50021"
DEFAULT_SPEAKER = 3  # ずんだもん ノーマル (起動中の ENGINE の /speakers で確認・変更可)
ENV_URL = "VOICEVOX_URL"
ENV_SPEAKER = "VOICEVOX_SPEAKER"
LAUNCH_HINT = "apps/tts-bench/README.md の手順で VOICEVOX ENGINE を起動してから再実行"


def parse_wav(data: bytes) -> tuple[bytes, int]:
    """WAV → (16-bit mono PCM, sample_rate)。想定外フォーマットは明確に落とす。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise RuntimeError(
                f"想定外の WAV 形式: {w.getsampwidth() * 8}bit {w.getnchannels()}ch "
                "(audio_query の outputStereo=false / 16bit を想定)"
            )
        return w.readframes(w.getnframes()), w.getframerate()


class VoicevoxClient:
    """audio_query / mora_data / synthesis の薄い同期クライアント。

    接続不可 (ENGINE 未起動) は EngineUnavailableError (起動案内つき)、
    API エラー (4xx/5xx) は RuntimeError として区別する。
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        speaker: int | None = None,
        synthesis_timeout_s: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get(ENV_URL, DEFAULT_URL)).rstrip("/")
        self.speaker = (
            speaker
            if speaker is not None
            else int(os.environ.get(ENV_SPEAKER, str(DEFAULT_SPEAKER)))
        )
        self.synthesis_timeout_s = synthesis_timeout_s

    def check(self) -> str | None:
        """ENGINE への疎通確認 (軽量)。None = 利用可、str = 不可の理由 (起動案内)。"""
        try:
            with urllib.request.urlopen(f"{self.base_url}/version", timeout=3) as r:
                r.read()
            return None
        except Exception as e:  # noqa: BLE001 — 接続不可は「未起動」として案内する
            return f"VOICEVOX ENGINE に接続できない ({self.base_url}): {e} — {LAUNCH_HINT}"

    def _post(self, path_query: str, body: bytes | None, timeout_s: float) -> bytes:
        headers = {"content-type": "application/json"} if body is not None else {}
        req = urllib.request.Request(
            f"{self.base_url}{path_query}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                data: bytes = r.read()
            return data
        except urllib.error.HTTPError as e:  # URLError のサブクラスなので先に捕捉
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"VOICEVOX API エラー {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise EngineUnavailableError(
                f"voicevox: ENGINE に接続できない ({self.base_url}): {e} — {LAUNCH_HINT}"
            ) from e

    def audio_query(self, text: str) -> dict[str, Any]:
        """text から合成クエリ (accent_phrases 含む JSON) を作る。"""
        q = urllib.parse.urlencode({"text": text, "speaker": self.speaker})
        query: dict[str, Any] = json.loads(self._post(f"/audio_query?{q}", None, 30.0))
        return query

    def mora_data(self, accent_phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """accent 編集後の accent_phrases のピッチ・長さを ENGINE に再計算させる。"""
        raw = self._post(
            f"/mora_data?speaker={self.speaker}", json.dumps(accent_phrases).encode(), 30.0
        )
        refreshed: list[dict[str, Any]] = json.loads(raw)
        return refreshed

    def synthesis(self, query: dict[str, Any]) -> bytes:
        """合成クエリ (アクセント編集後でも可) から WAV バイト列を合成する。"""
        return self._post(
            f"/synthesis?speaker={self.speaker}",
            json.dumps(query).encode(),
            self.synthesis_timeout_s,
        )

    def synthesize(self, text: str) -> tuple[bytes, int]:
        """text → (16-bit mono PCM, sample_rate) の一括合成 (既定クエリのまま)。"""
        return parse_wav(self.synthesis(self.audio_query(text)))
