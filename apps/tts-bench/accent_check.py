"""アクセント正確性チェック (tts-bench 固有の照合・採点層)。

G2P / アクセント句抽出 / ユーザー辞書の本体は `aiconv.frontend` (L0/L1) に集約した
(P1 で本モジュールから移設)。ここはテストセット (test_sentences) との照合だけを担う:

- 照合の前に L0 `normalize()` を通す (数字/時刻/英単語の正規化ケースを green にする層)。
- ユーザー辞書 (data/accent_dict) は predict_accent() が自動適用する (固有名詞の読み固定)。

表記 (expected_accent / 予測の共通フォーマット):
    "アメガ[1] フル[1]"  — 空白区切りのアクセント句。読みはカタカナモーラ列、
    [n] は核位置 (n モーラ目で下がる)、0 = 平板。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from test_sentences import TestSentence

from aiconv.frontend import (
    AccentPhrase,
    format_phrases,
    mora_count_kana,
    norm_kana,
    normalize,
    predict_accent,
    predicted_reading,
)

_EXPECTED = re.compile(r"^(\S+?)\[(\d+)\]$")


@dataclass(frozen=True, slots=True)
class AccentCheck:
    """1 文のチェック結果。expected が無い文は predicted の記録のみ。"""

    sentence_id: str
    predicted: str
    expected: str | None
    phrase_match_rate: float | None  # expected_accent がある文のみ
    reading_ok: bool | None  # expected_reading がある文のみ
    detail: str = ""


def parse_expected(spec: str) -> list[tuple[str, int]]:
    """expected_accent 文字列 → [(読み, 核位置)]。不正な書式は ValueError。"""
    out: list[tuple[str, int]] = []
    for token in spec.split():
        m = _EXPECTED.match(token)
        if m is None:
            raise ValueError(f"expected_accent の書式エラー: {token!r} (例: アメガ[1])")
        out.append((norm_kana(m.group(1)), int(m.group(2))))
    return out


def _phrase_matches(expected: tuple[str, int], predicted: AccentPhrase) -> bool:
    reading, accent = expected
    if accent != predicted.accent:
        return False
    if predicted.reading:
        return reading == predicted.reading
    # 読みが取れなかった句はモーラ数で代用比較
    return mora_count_kana(reading) == predicted.mora_count


def check_sentence(sentence: TestSentence) -> AccentCheck:
    """1 文を L0 正規化 → L1 予測し、expected_accent / expected_reading と照合する。"""
    phrases = predict_accent(normalize(sentence.text))
    predicted = format_phrases(phrases)

    rate: float | None = None
    details: list[str] = []
    if sentence.expected_accent:
        expected = parse_expected(sentence.expected_accent)
        if len(expected) != len(phrases):
            details.append(f"句数不一致 予測{len(phrases)}≠期待{len(expected)}")
        matched = 0
        for i, (e, p) in enumerate(zip(expected, phrases, strict=False), start=1):
            if _phrase_matches(e, p):
                matched += 1
            else:
                details.append(f"句{i}: 予測 {p.fmt()} ≠ 期待 {e[0]}[{e[1]}]")
        rate = matched / max(len(expected), len(phrases), 1)

    reading_ok: bool | None = None
    if sentence.expected_reading:
        got = predicted_reading(phrases)
        if got:  # 読みが取れなかった文は判定しない
            reading_ok = norm_kana(sentence.expected_reading) == got
            if not reading_ok:
                details.append(f"読み: 予測 {got} ≠ 期待 {norm_kana(sentence.expected_reading)}")

    return AccentCheck(
        sentence_id=sentence.id,
        predicted=predicted,
        expected=sentence.expected_accent,
        phrase_match_rate=rate,
        reading_ok=reading_ok,
        detail="; ".join(details),
    )
