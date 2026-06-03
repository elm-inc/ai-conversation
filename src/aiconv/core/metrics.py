"""レイテンシ計測 harness — 「発話終了 → 応答音声第一波」を段ごとに記録。

Phase 0 でベースラインを取り、各 Phase で回帰監視する (設計 §検証方法 1)。
体感遅延の定義 (設計レビュー反映): end_of_speech (ユーザー音声停止) → first_audio
(応答音声の最初のフレーム)。マークは単調時刻 (ms)。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

Clock = Callable[[], float]


def default_clock() -> float:
    """単調時刻 (ms)。"""
    return time.monotonic() * 1000.0


@dataclass
class TurnLatency:
    """1 ターン分の段階別タイムスタンプ (すべて ms)。最初のマークのみ採用する。"""

    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, t: float) -> None:
        self.marks.setdefault(name, t)

    def span(self, start: str, end: str) -> float | None:
        if start in self.marks and end in self.marks:
            return self.marks[end] - self.marks[start]
        return None

    @property
    def response_latency_ms(self) -> float | None:
        """体感遅延: end_of_speech → first_audio。"""
        return self.span("end_of_speech", "first_audio")


@dataclass
class LatencyRecorder:
    """ターンをまたいでレイテンシを蓄積する。"""

    clock: Clock = default_clock
    turns: list[TurnLatency] = field(default_factory=list)
    _current: TurnLatency | None = field(default=None, repr=False)

    def begin_turn(self) -> TurnLatency:
        self._current = TurnLatency()
        self.turns.append(self._current)
        return self._current

    def mark(self, name: str) -> float:
        if self._current is None:
            self.begin_turn()
        assert self._current is not None
        t = self.clock()
        self._current.mark(name, t)
        return t

    def report(self) -> str:
        if not self.turns:
            return "(no turns recorded)"
        lines: list[str] = []
        for i, turn in enumerate(self.turns):
            rl = turn.response_latency_ms
            if rl is not None:
                lines.append(f"turn {i}: response_latency={rl:.1f}ms")
            else:
                lines.append(f"turn {i}: (incomplete)")
            for stage in ("stt_final", "llm_ttft", "first_audio"):
                v = turn.span("end_of_speech", stage)
                if v is not None:
                    lines.append(f"    end_of_speech→{stage}: {v:.1f}ms")
        return "\n".join(lines)
