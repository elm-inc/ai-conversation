"""日本語 TTS フロントエンド (L0 テキスト正規化 + L1 G2P/アクセント)。

設計: docs/design/japanese-tts-optimization.md §5 (L0/L1)。
すべての TTS アダプタ・ハーネスが共有する中核層。pyopenjtalk-plus は遅延 import のため、
本パッケージ自体は依存なしで import できる (L0 の `normalize` は純粋関数)。

公開 API:
    normalize(text)            L0 のみ (記号/数字/英単語の表記正規化)
    predict_accent(text)       L1 のみ (アクセント句列。正規化済みテキスト前提)
    normalize_and_g2p(text)    L0 → L1 の統合 (FrontendResult)
    load_user_dict(), ensure_user_dict(), reset_user_dict()   ユーザー辞書 (data/accent_dict)
    frontend_available()       pyopenjtalk 導入チェック (None=可 / str=導入案内)
"""

from __future__ import annotations

from dataclasses import dataclass

from .accent import (
    ENV_DICT_DIR,
    ENV_RUN_MARINE,
    AccentPhrase,
    compile_user_dict,
    ensure_user_dict,
    find_dict_dir,
    format_phrases,
    frontend_available,
    g2p_phonemes,
    load_user_dict,
    mora_count_kana,
    norm_kana,
    predict_accent,
    predicted_reading,
    reset_user_dict,
)
from .text_normalize import KNOWN_WORDS, normalize, register_words

__all__ = [
    "ENV_DICT_DIR",
    "ENV_RUN_MARINE",
    "KNOWN_WORDS",
    "AccentPhrase",
    "FrontendResult",
    "compile_user_dict",
    "ensure_user_dict",
    "find_dict_dir",
    "format_phrases",
    "frontend_available",
    "g2p_phonemes",
    "load_user_dict",
    "mora_count_kana",
    "norm_kana",
    "normalize",
    "normalize_and_g2p",
    "predict_accent",
    "predicted_reading",
    "register_words",
    "reset_user_dict",
]


@dataclass(frozen=True, slots=True)
class FrontendResult:
    """L0+L1 の統合結果。text は正規化後 (= G2P に渡した) テキスト。"""

    text: str
    phrases: tuple[AccentPhrase, ...]  # アクセント句列 (読み/核位置/モーラ数)
    phonemes: tuple[str, ...]  # 音素列 (pau 含む, sil 除く)

    @property
    def reading(self) -> str:
        """文全体の読み (カタカナ連結)。"""
        return predicted_reading(list(self.phrases))

    def fmt_phrases(self) -> str:
        """アクセント句列の表示形式 "アメガ[1] フルラシイヨ[1]" (0=平板)。"""
        return format_phrases(list(self.phrases))


def normalize_and_g2p(text: str, *, run_marine: bool | None = None) -> FrontendResult:
    """LLM 出力テキスト → L0 正規化 → L1 G2P (アクセント句 + 音素列)。

    TTS 合成・アクセント検証の標準入口。pyopenjtalk 未導入なら ImportError
    (事前に `frontend_available()` で確認するか、呼び出し側で skip する)。
    """
    normalized = normalize(text)
    phrases = predict_accent(normalized, run_marine=run_marine)
    phonemes = g2p_phonemes(normalized, run_marine=run_marine)
    return FrontendResult(text=normalized, phrases=tuple(phrases), phonemes=tuple(phonemes))
