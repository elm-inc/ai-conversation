"""アクセント正確性チェック (エンジン非依存・フロントエンド層)。

pyopenjtalk(-plus) のフルコンテキストラベルから各文のアクセント句列
(モーラ読み + アクセント核位置) を抽出し、test_sentences の expected_accent と照合する。
「根治の核 = フロントエンドのアクセント」をエンジン抜きで検証する層
(設計 japanese-tts-optimization §P0)。pyopenjtalk-plus は MIT で商用クリーン。

表記 (expected_accent / 予測の共通フォーマット):
    "アメガ[1] フル[1]"  — 空白区切りのアクセント句。読みはカタカナモーラ列、
    [n] は核位置 (n モーラ目で下がる)、0 = 平板。

抽出ロジック:
- アクセント句境界は /F:...@f5_ の f5 (呼気段落内のアクセント句位置) の変化 +
  pau/sil による呼気段落の切り替わりで検出する (HTS 日本語ラベル仕様)。
- モーラ数 = f1、アクセント型 = f2 (/F:f1_f2#...)。
- OpenJTalk は平板型を「核位置 = モーラ数」(句末モーラ) で表現する (実測:
  飴が → /F:3_3)。句内では下降が起きず音響的に平板と等価なので、表記の慣行に
  合わせて 0 に正規化する。
- 読みは run_frontend の NJD pron を chain_flag で句に結合して充てる。句数が合わない
  場合は読みなし (モーラ数表示) にフォールバックする。
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass

from test_sentences import TestSentence

_LBL_PHONE = re.compile(r"\-(.+?)\+")
_LBL_F = re.compile(r"/F:(\d+)_(\d+)#\d+_\d+@(\d+)_")  # f1=モーラ数, f2=アクセント型, f5=句位置
_EXPECTED = re.compile(r"^(\S+?)\[(\d+)\]$")
# 拗音等の小書きは直前のカナと合わせて 1 モーラ ("ッ"/"ー"/"ン" は独立モーラ)
_SMALL_KANA = set("ャュョァィゥェォヮ")


def frontend_available() -> str | None:
    """pyopenjtalk が import 可能か。None = 可、str = 不可の理由 (導入案内)。"""
    if importlib.util.find_spec("pyopenjtalk") is None:
        return (
            "pyopenjtalk 未導入のためアクセントチェックをスキップ: "
            "uv sync --inexact --extra tts-bench を実行 (pyopenjtalk-plus, MIT)"
        )
    return None


@dataclass(frozen=True, slots=True)
class AccentPhrase:
    """アクセント句 = 読み (カタカナ) + アクセント核位置 (0=平板) + モーラ数。"""

    reading: str  # NJD と整合しなかった場合は "" (モーラ数のみで比較)
    accent: int
    mora_count: int

    def fmt(self) -> str:
        label = self.reading or f"({self.mora_count}モーラ)"
        return f"{label}[{self.accent}]"


@dataclass(frozen=True, slots=True)
class AccentCheck:
    """1 文のチェック結果。expected が無い文は predicted の記録のみ。"""

    sentence_id: str
    predicted: str
    expected: str | None
    phrase_match_rate: float | None  # expected_accent がある文のみ
    reading_ok: bool | None  # expected_reading がある文のみ
    detail: str = ""


def _norm_kana(s: str) -> str:
    """比較用正規化: 無声化記号・空白を除去。"""
    return s.replace("’", "").replace(" ", "")


def _mora_count_kana(reading: str) -> int:
    return sum(1 for ch in _norm_kana(reading) if ch not in _SMALL_KANA)


def _phrase_readings(text: str) -> list[str]:
    """NJD ノードを chain_flag でアクセント句に結合した読み (カタカナ) のリスト。"""
    import pyopenjtalk

    readings: list[str] = []
    for node in pyopenjtalk.run_frontend(text):
        pron = str(node.get("pron") or "")
        if int(node.get("mora_size", 0)) <= 0 or not pron:
            continue  # 記号 (、。等) は句を構成しない
        if int(node.get("chain_flag", 0)) == 1 and readings:
            readings[-1] += pron
        else:
            readings.append(pron)
    return readings


def predict_accent(text: str) -> list[AccentPhrase]:
    """pyopenjtalk のフルコンテキストラベルからアクセント句列を予測する。"""
    import pyopenjtalk

    phrases: list[tuple[int, int]] = []  # (accent, mora_count)
    breath_group = 0
    cur_key: tuple[int, int] | None = None
    for lab in pyopenjtalk.extract_fullcontext(text):
        m = _LBL_PHONE.search(lab)
        if m is None or m.group(1) in ("sil", "pau"):
            breath_group += 1
            cur_key = None
            continue
        mf = _LBL_F.search(lab)
        if mf is None:  # 想定外ラベルは無視
            continue
        mora_count, accent, f5 = int(mf.group(1)), int(mf.group(2)), int(mf.group(3))
        if accent == mora_count:  # OpenJTalk の平板表現 (句末核) → 0 に正規化
            accent = 0
        key = (breath_group, f5)
        if key != cur_key:
            phrases.append((accent, mora_count))
            cur_key = key
    readings = _phrase_readings(text)
    if len(readings) != len(phrases):  # NJD と整合しない場合は読みなしにフォールバック
        readings = [""] * len(phrases)
    return [
        AccentPhrase(reading=_norm_kana(r), accent=acc, mora_count=n)
        for r, (acc, n) in zip(readings, phrases, strict=True)
    ]


def format_phrases(phrases: list[AccentPhrase]) -> str:
    return " ".join(p.fmt() for p in phrases)


def predicted_reading(phrases: list[AccentPhrase]) -> str:
    """文全体の予測読み (カタカナ連結)。"""
    return "".join(p.reading for p in phrases)


def parse_expected(spec: str) -> list[tuple[str, int]]:
    """expected_accent 文字列 → [(読み, 核位置)]。不正な書式は ValueError。"""
    out: list[tuple[str, int]] = []
    for token in spec.split():
        m = _EXPECTED.match(token)
        if m is None:
            raise ValueError(f"expected_accent の書式エラー: {token!r} (例: アメガ[1])")
        out.append((_norm_kana(m.group(1)), int(m.group(2))))
    return out


def _phrase_matches(expected: tuple[str, int], predicted: AccentPhrase) -> bool:
    reading, accent = expected
    if accent != predicted.accent:
        return False
    if predicted.reading:
        return reading == predicted.reading
    # 読みが取れなかった句はモーラ数で代用比較
    return _mora_count_kana(reading) == predicted.mora_count


def check_sentence(sentence: TestSentence) -> AccentCheck:
    """1 文を予測し、expected_accent / expected_reading と照合する。"""
    phrases = predict_accent(sentence.text)
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
            reading_ok = _norm_kana(sentence.expected_reading) == got
            if not reading_ok:
                details.append(f"読み: 予測 {got} ≠ 期待 {_norm_kana(sentence.expected_reading)}")

    return AccentCheck(
        sentence_id=sentence.id,
        predicted=predicted,
        expected=sentence.expected_accent,
        phrase_match_rate=rate,
        reading_ok=reading_ok,
        detail="; ".join(details),
    )
