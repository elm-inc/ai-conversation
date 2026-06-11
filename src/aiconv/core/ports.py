"""交換可能なポート (安定インターフェース) — ADR-0002。

各 Protocol はベンダー非依存。アダプタが実装し、ベンダー固有の癖 (partial の不安定さ・
遅延ばらつき・日本語特性・flush 挙動) を吸収する。コアは core.events の型のみを介して
外部とやり取りする。

ポート粒度と内部標準イベント型の凍結は Phase 0 の最優先タスク (設計 §未確定の方針判断)。
ここでは v1 として確定し、以後は加法的バージョニング + optional capability で進化させる。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .events import AudioFrame, TokenChunk, Transcript, TurnDecision


@dataclass(frozen=True, slots=True)
class Capability:
    """Provider が宣言する能力。コアは能力に応じてフォールバックする。

    新機能は optional フィールドを足して加法的に拡張する (破壊的変更を避ける)。
    """

    streaming_partials: bool = False
    supports_interrupt: bool = False
    flush_latency_ms: float | None = None
    languages: tuple[str, ...] = ()
    notes: str = ""
    # 静的に判明する利用可否 (ライブラリ未導入等)。False でも import/生成は成功させ、
    # 実呼び出し時に導入案内つきの明確な例外を出す (セルフホスト TTS アダプタの規約)。
    available: bool = True


@runtime_checkable
class AuditSink(Protocol):
    """TTS 合成の監査ログ吸い込み口 (声優音源の許諾追跡)。"""

    def record(self, *, voice_id: str, text: str, allowed: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class VoiceLicense:
    """声優音源の許諾範囲ガード。

    (設計レビュー反映: アダプタ層で許諾範囲を強制し、声優人格権侵害を構造的に防ぐ)
    """

    voice_id: str
    allow: Callable[[str], bool]  # 合成してよいテキストか判定

    def permits(self, text: str) -> bool:
        return self.allow(text)


@runtime_checkable
class STTProvider(Protocol):
    """音声フレーム列 → transcript 列 (partial/final, 健全性フラグ付き)。"""

    capabilities: Capability

    def transcribe(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Transcript]: ...


@runtime_checkable
class TTSProvider(Protocol):
    """テキストチャンク列 → 音声フレーム列。interrupt/flush・声優ボイス対応。"""

    capabilities: Capability

    def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioFrame]: ...

    async def interrupt(self) -> None: ...


@runtime_checkable
class LLMProvider(Protocol):
    """messages + persona → token stream。TTFT を露出 (TokenChunk.is_first)。"""

    capabilities: Capability

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        system: str = "",
        cache_hint: bool = True,
    ) -> AsyncIterator[TokenChunk]: ...


@runtime_checkable
class TurnDetector(Protocol):
    """(partial transcript, 無音長) → ターン判定。Phase 1 で semantic 化する。

    `during_speech=True` は AI 発話中の判定 (barge-in 用)。相槌は BACKCHANNEL、
    実質的な割り込みは BARGE_IN を返す。
    """

    def predict(
        self,
        partial: Transcript | None,
        *,
        silence_ms: float,
        during_speech: bool = False,
    ) -> TurnDecision: ...


@runtime_checkable
class AudioTransport(Protocol):
    """WebRTC/WS の音声 I/O。Pipecat/LiveKit はこのポート背後の一アダプタ。"""

    def inbound(self) -> AsyncIterator[AudioFrame]: ...

    async def play(self, frame: AudioFrame) -> None: ...

    async def stop_playback(self) -> None: ...


@runtime_checkable
class FillerProvider(Protocol):
    """相槌/フィラーの音声フレームを供給する (レイテンシ隠蔽用)。

    LLM の TTFT を「うーん」「なるほど」等で埋め、本応答が用意でき次第ストップする。
    本応答が来るまで流せるよう、十分な長さ (またはループした) フレーム列を yield する。
    """

    def filler(self) -> AsyncIterator[AudioFrame]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """text → vector (記憶検索用)。"""

    async def embed(self, text: str) -> list[float]: ...


@runtime_checkable
class MemoryStore(Protocol):
    """構造化された関係状態の書込/検索 (Phase 3 で本実装)。

    (設計レビュー反映: 追記ログではなく競合解決・忘却・PII マスキング前提)
    """

    async def write_episode(
        self, summary: str, *, embedding: list[float] | None = None
    ) -> None: ...

    async def search(self, query: str, *, k: int = 5) -> list[str]: ...
