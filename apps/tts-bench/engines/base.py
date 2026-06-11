"""ベンチ用 TTS エンジンの共通インタフェース。

src/aiconv/core/ports.py の TTSProvider (async ストリーミング) とは別物の、選定ベンチ専用の
薄い同期インタフェース。本採用が決まったエンジンは改めて TTSProvider アダプタとして
src/aiconv/adapters/ に実装する (このハーネスは比較・計測用)。

規約 (graceful degradation):
- 各エンジンモジュールは import 時に重い処理 (モデル DL / GPU 確保 / SDK import) をしない。
- `check()` で利用可否を返し、不可ならハーネスはそのエンジンを skip して続行する。
- 重いロードは `prepare()` (または初回 `synthesize()`) まで遅延する。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


def read_token(name: str) -> str:
    """~/.{name}_token 方式の API キー読込 (conversation-tester director._tok と同じ規約)。"""
    p = Path(f"~/.{name}_token").expanduser()
    return p.read_text().strip() if p.is_file() else ""


class EngineUnavailableError(RuntimeError):
    """エンジンが利用不可 (未インストール / サーバ未起動 / API キー無し)。

    ハーネスはこれを捕捉してエンジンを skip する (全体は止めない)。
    """


@dataclass(frozen=True, slots=True)
class SynthResult:
    """1 文の合成結果 + 内部計測。"""

    pcm: bytes  # 16-bit mono PCM
    sample_rate: int
    duration_s: float  # 生成音声長 (秒)
    ttfa_ms: float  # 合成要求 → 最初の音声バイト (非ストリーミングでは elapsed と同値)
    elapsed_ms: float  # 合成要求 → 完了
    streaming: bool  # True ならチャンク到着ベースの TTFA
    notes: str = ""


class Engine(abc.ABC):
    """各 TTS エンジンのベンチアダプタ基底。"""

    name: ClassVar[str] = "base"

    @abc.abstractmethod
    def check(self) -> str | None:
        """利用可否を確認する。None = 利用可能、str = 利用不可の理由 (導入手順の案内)。

        ネットワーク疎通程度の軽い確認のみ。モデルロードはしない。
        """

    def prepare(self) -> None:
        """重い初期化 (モデルロード等)。計測対象外としてハーネスが 1 回だけ呼ぶ。"""
        return None

    @abc.abstractmethod
    def synthesize(self, text: str) -> SynthResult:
        """text を合成して計測付きで返す。利用不可なら EngineUnavailableError。"""
