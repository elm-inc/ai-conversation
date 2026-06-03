"""素朴なターン検出 (無音ベース) — Phase 0。

Phase 1 で日本語 semantic endpointing (テキスト意味 + 音響韻律の両 signal) に置換する。
ここでは無音長が閾値以上かつ transcript が空でなければ COMPLETE とするだけ。
"""

from __future__ import annotations

from ..core.events import Transcript, TurnDecision, TurnLabel


class SilenceTurnDetector:
    """無音長による素朴な終端判定。"""

    def __init__(self, *, threshold_ms: float = 600.0) -> None:
        self.threshold_ms = threshold_ms

    def predict(self, partial: Transcript | None, *, silence_ms: float) -> TurnDecision:
        if partial is None or not partial.text.strip():
            return TurnDecision(TurnLabel.INCOMPLETE, prob=1.0, at_ms=None)
        label = TurnLabel.COMPLETE if silence_ms >= self.threshold_ms else TurnLabel.INCOMPLETE
        return TurnDecision(label, prob=1.0, at_ms=None)
