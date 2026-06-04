"""内部標準イベント型 — ベンダー非依存コアが扱う唯一のデータ語彙。

ADR-0002 (ports & adapters) の中核。各アダプタはベンダー固有の表現をここで定義する
型へ正規化してからコアに渡す。コアはベンダー SDK の型を一切知らない。

この語彙の安定性が「検証で差し替える」運用の前提 (設計 §抽象化レイヤー)。
変更は破壊的変更を避ける加法的バージョニングで行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TranscriptHealth(StrEnum):
    """STT 出力の健全性。アダプタ境界で判定し、異常は応答ゲートで止める。

    (設計レビュー反映: ノイズ誤認識・繰り返し・異常長で投機応答が暴走するのを防ぐ)
    """

    OK = "ok"
    LOW_CONFIDENCE = "low_confidence"
    ABNORMAL = "abnormal"


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """PCM 音声フォーマット。"""

    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2  # bytes/sample (16-bit PCM)


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """音声フレーム。`ts_ms` はコア基準の単調時刻 (発話タイムライン上の位置)。"""

    data: bytes
    ts_ms: float
    seq: int = 0
    fmt: AudioFormat = field(default_factory=AudioFormat)

    @property
    def duration_ms(self) -> float:
        n_samples = len(self.data) / (self.fmt.sample_width * self.fmt.channels)
        return n_samples / self.fmt.sample_rate * 1000.0


@dataclass(frozen=True, slots=True)
class Transcript:
    """STT の認識結果イベント (partial または final)。"""

    text: str
    is_final: bool
    confidence: float = 1.0
    start_ms: float | None = None
    end_ms: float | None = None
    health: TranscriptHealth = TranscriptHealth.OK
    # 音響シグナル: プロバイダが「発話が終わった」と判断したヒント。
    # アダプタがベンダー固有信号 (Deepgram speech_final 等) を正規化して載せる。
    # ターン検出 (FusionTurnDetector) がテキスト意味と融合して使う。
    endpoint_hint: bool = False

    @property
    def is_usable(self) -> bool:
        """応答生成に使ってよい transcript か (健全性ゲート)。"""
        return self.health is not TranscriptHealth.ABNORMAL and bool(self.text.strip())


@dataclass(frozen=True, slots=True)
class TokenChunk:
    """LLM のストリーミング出力トークン (差分)。"""

    text: str
    index: int
    is_first: bool = False
    is_last: bool = False


class TurnLabel(StrEnum):
    """ターンテイキングの判定ラベル。"""

    COMPLETE = "complete"        # 発話完了 — 応答してよい
    INCOMPLETE = "incomplete"    # まだ続く — 待つ
    BACKCHANNEL = "backchannel"  # 相槌 — ターンを奪わない
    BARGE_IN = "barge_in"        # 割り込み — AI 発話中なら即停止


@dataclass(frozen=True, slots=True)
class TurnDecision:
    """ターン検出の判定結果。"""

    label: TurnLabel
    prob: float = 1.0
    at_ms: float | None = None

    @property
    def is_complete(self) -> bool:
        return self.label is TurnLabel.COMPLETE


class SpeechState(StrEnum):
    """対話 FSM の状態 (設計 §中核アーキテクチャ [2])。"""

    IDLE = "idle"
    LISTEN = "listen"
    THINK = "think"
    SPEAK = "speak"
