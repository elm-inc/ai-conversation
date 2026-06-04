"""融合ターン検出器 — テキスト意味 + 音響シグナルの両 signal を統合 (Phase 1 主軸)。

入力 signal:
- テキスト意味: core.endpointing.text_completion_score (日本語の文末助詞/助詞止め/フィラー)
- 音響: Transcript.endpoint_hint (Deepgram speech_final 等をアダプタが正規化) + 無音長

競合裁定 (設計の融合方針):
- 相槌の単独短発話 → BACKCHANNEL (ターンを奪わない)
- フィラーで終わる → INCOMPLETE (まだ続く)
- テキストが明確に完結 → COMPLETE
- 音響が終端を示す & テキストが助詞止め等 → ターン放棄とみなし COMPLETE (短い猶予つき)
- 音響が終端を示すがテキストが断片すぎる → INCOMPLETE (ノイズ/言いかけ)
- 無音が伸びて & テキスト中程度 → COMPLETE

しきい値はチューニング可能。TurnDetector ポートを満たすので素朴版から学習版へ差し替え可。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.endpointing import (
    is_backchannel,
    is_filler_only,
    looks_like_interruption,
    text_completion_score,
)
from ..core.events import Transcript, TurnDecision, TurnLabel


@dataclass
class FusionThresholds:
    complete_text: float = 0.8       # これ以上なら音響を待たず完結
    yield_text: float = 0.3          # 音響終端時にターン放棄とみなす最低テキストスコア
    midlevel_text: float = 0.5       # 無音継続時に完結とみなす最低テキストスコア
    short_silence_ms: float = 500.0  # 文末助詞などと併用する短い無音
    long_silence_ms: float = 900.0   # テキストが弱くても完結とみなす長い無音


class FusionTurnDetector:
    """テキスト意味 + 音響 (endpoint_hint / 無音長) の融合判定。"""

    def __init__(self, thresholds: FusionThresholds | None = None) -> None:
        self.th = thresholds or FusionThresholds()

    def predict(
        self,
        partial: Transcript | None,
        *,
        silence_ms: float,
        during_speech: bool = False,
    ) -> TurnDecision:
        if partial is None or not partial.text.strip():
            return TurnDecision(TurnLabel.INCOMPLETE, prob=1.0)

        text = partial.text

        # AI 発話中: 相槌は聞き流し、実質的な発話/割り込み語は BARGE_IN
        if during_speech:
            if is_backchannel(text) or is_filler_only(text):
                return TurnDecision(TurnLabel.BACKCHANNEL, prob=0.9)
            if (
                looks_like_interruption(text)
                or partial.endpoint_hint
                or text_completion_score(text) >= self.th.yield_text
            ):
                return TurnDecision(TurnLabel.BARGE_IN, prob=0.85)
            return TurnDecision(TurnLabel.INCOMPLETE, prob=0.6)

        # 相槌の単独短発話はターンを奪わない
        if is_backchannel(text):
            return TurnDecision(TurnLabel.BACKCHANNEL, prob=0.9)
        # フィラーのみ → まだ続く
        if is_filler_only(text):
            return TurnDecision(TurnLabel.INCOMPLETE, prob=0.9)

        score = text_completion_score(text)
        audio_end = partial.endpoint_hint or silence_ms >= self.th.long_silence_ms

        # テキストが明確に完結 → 音響を待たず COMPLETE
        if score >= self.th.complete_text:
            return TurnDecision(TurnLabel.COMPLETE, prob=score)

        if audio_end:
            # 音響が終端を示す。テキストが助詞止め等でも、最低限の中身があればターン放棄と解釈
            if score >= self.th.yield_text:
                return TurnDecision(TurnLabel.COMPLETE, prob=max(score, 0.6))
            # 断片すぎる (ノイズ/言いかけ) → まだ待つ
            return TurnDecision(TurnLabel.INCOMPLETE, prob=0.6)

        # 音響は終端を示さないが、短い無音 + 中程度テキストなら完結
        if silence_ms >= self.th.short_silence_ms and score >= self.th.midlevel_text:
            return TurnDecision(TurnLabel.COMPLETE, prob=score)

        return TurnDecision(TurnLabel.INCOMPLETE, prob=1.0 - score)
