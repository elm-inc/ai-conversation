"""日本語 TTS フロントエンド L0 — テキスト正規化 (設計 japanese-tts-optimization §5-L0)。

LLM 出力テキスト (記号 / 英数字 / 絵文字 / マークダウン混じり) を、G2P (L1) が
正しく読み下せる日本語表記へ正規化する。アクセントはここでは扱わない (L1 の責務)。

方針:
- **数字はアラビア数字のまま「表記の整形」に徹する**。pyopenjtalk-plus (NAIST-JDIC) の
  数値読みは助数詞の音便まで正確 (14時=ジューヨジ, 30分=サンジュップン,
  3000円=サンゼンエン) なため、漢数字への変換はかえって助数詞文脈を壊す。
  L0 が潰すのは読みを崩す表記: 桁区切りカンマ / 時刻コロン / 日付スラッシュ /
  単位・通貨記号 / 範囲記号。
- **英単語は既知語辞書 (KNOWN_WORDS) でカタカナ化**。未知語は L1 側のフォールバック
  (NAIST-JDIC は USB=ユーエスビー, NASA=ナサ 等を収録済み。未知の英字列はレター読み)
  に委ねる。既知語の恒久追加は本モジュールの KNOWN_WORDS を PR で更新する。
- **マークダウン / 絵文字 / 記号** は除去または読み替え (コードブロックは除去、
  リンクはテキストだけ残す、矢印は読点化、E=mc^2 は「イコール」「の2乗」)。
- **英略語のピリオド保護**: `A.1` → `A1`、`Mr.` 等は既知語辞書で読み替え、
  下流の文分割 (`_sentences()` 等) が文境界と誤認しないようにする。

既知の取りこぼし (意図的に未対応。残課題として記録):
- 単独の `M/D` 日付はヒューリスティックで変換する (`6/11` → `6月11日`) が、
  **分数と区別できない曖昧域は変換しない**: 月<日 かつ 日≤10 (例 `6/8`, `1/2`, `1/10`)。
  ゼロ埋め (`06/08`) か曜日付き (`6/8(土)`) なら日付確定として変換する。
  逆に「確率は6/11だ」のような分数 (日>10) は日付に誤変換される (会話文では
  日付が圧倒的に多いトレードオフとして許容)。分数の読み下し (1/2 → 2ぶんの1) は未対応。
- 電話番号は `0X-XXXX-XXXX` 形式 (先頭 0 + ハイフン 2 個) のみ数字読みに変換する。
  市外局番なし (`1234-5678`) やハイフンなしの 10-11 桁は番号と確信できないため未変換。
- 乗算は `×` `✕` `✖` `*` を「かける」に読み替える (数字に挟まれた場合のみ)。
  `3x4` の英字 x は変数/ヘクス表記と区別できないため未対応。
- 顔文字 `(´・ω・`)` の完全除去は対象外 (記号掃除で部分的に消える)。
根本的には LLM 側プロンプトを「音声読み上げ前提」にするのが本筋で、L0 はその安全網。
"""

from __future__ import annotations

import re
import unicodedata

# --- 英単語・略語の既知語辞書 (表層 → 読み表記)。長い表層から優先して照合する。 ---
# pyopenjtalk (NAIST-JDIC) が誤読する語だけでなく、エンジン非依存で読みを固定したい
# プロジェクト頻出語も登録する。読みはカタカナ (または自然な日本語表記)。
KNOWN_WORDS: dict[str, str] = {
    "OpenAI": "オープンエーアイ",
    "ChatGPT": "チャットジーピーティー",
    "GPT": "ジーピーティー",
    "AI": "エーアイ",
    "Claude": "クロード",
    "Anthropic": "アンソロピック",
    "Gemini": "ジェミニ",
    "DeepSeek": "ディープシーク",
    "LLM": "エルエルエム",
    "TTS": "ティーティーエス",
    "Wi-Fi": "ワイファイ",
    "WiFi": "ワイファイ",
    "YouTube": "ユーチューブ",
    "iPhone": "アイフォン",
    "Google": "グーグル",
    "Netflix": "ネットフリックス",
    "API": "エーピーアイ",
    "SNS": "エスエヌエス",
    "URL": "ユーアールエル",
    # 英略語ピリオドの文境界誤認を防ぐ読み替え (設計 §5-L0 エッジケース)
    "Mr.": "ミスター",
    "Dr.": "ドクター",
    "e.g.": "例えば",
    "i.e.": "すなわち",
    "etc.": "エトセトラ",
    "vs.": "対",
}

# 数字に後続する単位記号の読み (長い表層を先に照合)。℃ は NFKC で °C になるため両方持つ。
_UNIT_READINGS: dict[str, str] = {
    "km": "キロメートル",
    "kg": "キログラム",
    "cm": "センチメートル",
    "mm": "ミリメートル",
    "ml": "ミリリットル",
    "mg": "ミリグラム",
    "°C": "度",
    "℃": "度",
    "%": "パーセント",
    "m": "メートル",
    "g": "グラム",
    "L": "リットル",
}

# --- マークダウン ---
_FENCED_CODE = re.compile(r"```.*?(?:```|$)", re.DOTALL)  # コードブロックは読まない (除去)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")  # インラインコードは中身だけ残す
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_HR = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d{1,2}[.)])\s+", re.MULTILINE)
_MD_QUOTE = re.compile(r"^\s*>+\s?", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*{1,3}|~~)([^*~\n]+?)\1")
_URL = re.compile(r"https?://\S+|www\.\S+")

# --- 絵文字・顔文字系 (読み上げない → 除去) ---
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 絵文字ブロック一帯 (麻雀牌〜Symbols Ext)
    "\u2600-\u27bf"  # Misc Symbols / Dingbats (太陽・チェック・キラキラ等)
    "\u2b00-\u2bff"  # Misc Symbols and Arrows (太矢印・星等)
    "\u2190-\u21ff"  # 矢印 (→ は先に読点へ変換済み)
    "\ufe00-\ufe0f"  # 異体字セレクタ (絵文字 VS16 等)
    "\u200d"  # ZWJ (絵文字合成)
    "\u20e3"  # 囲み keycap
    "\u00a9\u00ae\u2122"  # (c) (R) TM
    "\u3030\u303d"  # 波ダッシュ装飾・庵点
    "]+"
)
_LAUGH_MARK = re.compile(r"[(（](?:笑|泣|怒|汗|照|涙)[)）]")
_W_LAUGH = re.compile(r"(?<![A-Za-z])[wｗ]{2,}(?![A-Za-z.])")  # 草 (ww) は読まない

# --- 数字・日付・時刻・範囲・単位・通貨 ---
_GROUPED_COMMA = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")  # 3,000 → 3000 (桁区切りのみ)
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?![\d/-])")
# 単独 M/D (6/11 → 6月11日)。分数との曖昧域はヒューリスティックで安全側に倒す (docstring)。
_DATE_MD = re.compile(
    r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])(?:[(（](月|火|水|木|金|土|日)[)）])?"
)
# 電話番号 (0X(X(X))-XX(XX)-XXX(X))。数字読みカタカナへ展開 (1234 をセン... と読ませない)
_PHONE = re.compile(r"(?<![\d-])(0\d{1,3})-(\d{2,4})-(\d{3,4})(?![\d-])")
_PHONE_DIGITS = {
    "0": "ゼロ", "1": "イチ", "2": "ニー", "3": "サン", "4": "ヨン",
    "5": "ゴー", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュー",
}
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
_RANGE_WAVE = re.compile(r"(?<=\d)\s*[〜~～]\s*(?=\d)")  # 3〜4 → 3から4
_RANGE_HYPHEN = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?![\d-])")  # 3-4 → 3から4
_UNIT = re.compile(
    "(?<=\\d)\\s*(" + "|".join(re.escape(u) for u in _UNIT_READINGS) + ")(?![A-Za-z])"
)
_CURRENCY_YEN = re.compile(r"[¥￥](\d+)")  # ¥3000 → 3000円
_CURRENCY_DOLLAR = re.compile(r"\$(\d+(?:\.\d+)?)")  # $5 → 5ドル

# --- 記号の読み規則 (設計 §5-L0 エッジケース: E=mc^2, ->, | など) ---
_ABBREV_DOT = re.compile(r"(?<![A-Za-z0-9.])([A-Za-z])\.(?=\d)")  # A.1 → A1 (文境界保護)
_ARROW = re.compile(r"->|=>|⇒|→")  # 矢印は間 (読点) に読み替える
# 乗算記号 (数字に挟まれた場合のみ)。✕✖ は絵文字除去 (U+2600-27BF) より先に読み替える
_MULTIPLY = re.compile(r"(?<=\d)\s*[×✕✖*]\s*(?=\d)")
_POWER = re.compile(r"\^(\d+)")  # x^2 → xの2乗
_EQUALS = re.compile(r"(?<=\w)\s*=\s*(?=\w)")  # E=mc → Eイコールmc
_SILENT_SYMBOLS = re.compile(r"[`*_#|<>{}\[\]\\~^¥$@;:+]+")  # 読まない記号は間引く
_NEWLINES = re.compile(r"[ \t]*\n+[ \t]*")
_SPACES = re.compile(r"[ \t　]+")

_words_pattern: re.Pattern[str] | None = None


def register_words(words: dict[str, str]) -> None:
    """既知語辞書へ追記する (プロセス内のみ)。恒久追加は KNOWN_WORDS を PR で更新する。"""
    global _words_pattern
    KNOWN_WORDS.update(words)
    _words_pattern = None  # パターン再構築


def _word_regex() -> re.Pattern[str]:
    global _words_pattern
    if _words_pattern is None:
        keys = sorted(KNOWN_WORDS, key=len, reverse=True)  # 長い表層を優先 (OpenAI > AI)
        body = "|".join(re.escape(k) for k in keys)
        # 英数字の途中では照合しない (OpenAI 内の AI を拾わない)。日本語隣接は許す
        # (\b は Unicode の語境界のため「AIとか」で効かない)。
        _words_pattern = re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.IGNORECASE)
    return _words_pattern


def _word_repl(m: re.Match[str]) -> str:
    lower_map = {k.lower(): v for k, v in KNOWN_WORDS.items()}
    return lower_map[m.group(0).lower()]


def _date_repl(m: re.Match[str]) -> str:
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)  # 日付として不正なら触らない
    return f"{y}年{mo}月{d}日"


def _date_md_repl(m: re.Match[str]) -> str:
    """単独 M/D → M月D日。分数と曖昧なケース (月<日 かつ 日≤10) は変換しない。

    ゼロ埋め (06/08) と曜日付き (6/8(土)) は分数では使われないため日付確定とする。
    曜日は「土曜日」と読み下す (日付に付随する場合のみ。単独の (土) は触らない)。
    """
    mo, d, weekday = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)  # 日付として不正 (13/45 等) なら触らない
    zero_padded = m.group(1).startswith("0") or m.group(2).startswith("0")
    if mo < d and d <= 10 and not zero_padded and weekday is None:
        return m.group(0)  # 分数 (1/2, 6/8) の可能性が高い曖昧域 → 安全側で未変換
    out = f"{mo}月{d}日"
    return out + (f"{weekday}曜日" if weekday else "")


def _phone_repl(m: re.Match[str]) -> str:
    """電話番号 → 数字読みカタカナ (NTT 風: 2=ニー, 5=ゴー)。ハイフンは「の」。"""
    return "の".join(
        "".join(_PHONE_DIGITS[c] for c in m.group(i)) for i in (1, 2, 3)
    )


def _time_repl(m: re.Match[str]) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    s = int(m.group(3)) if m.group(3) else None
    if h > 24 or mi > 59 or (s is not None and s > 59):
        return m.group(0)  # 時刻として不正なら触らない (スコア表記など)
    out = f"{h}時"
    if mi:
        out += f"{mi}分"
    if s:
        out += f"{s}秒"
    return out


def _unit_repl(m: re.Match[str]) -> str:
    return _UNIT_READINGS[m.group(1)]


def _newline_repl(m: re.Match[str]) -> str:
    """改行は文境界として句点に読み替える (直前が文末記号なら単純結合)。"""
    prev = m.string[m.start() - 1] if m.start() > 0 else ""
    return "" if (not prev or prev in "。、！？!?") else "。"


def normalize(text: str) -> str:
    """LLM 出力テキストを読み上げ用の日本語表記へ正規化する (L0)。

    出力はそのまま L1 (`aiconv.frontend.accent`) や任意の TTS エンジンに渡せる。
    純粋関数 (副作用なし)。pyopenjtalk 等の外部依存も不要。
    """
    t = _FENCED_CODE.sub("", text)
    t = unicodedata.normalize("NFKC", t)  # 全角英数/単位合字 (㌫等) を半角へ折りたたむ
    # マークダウン
    t = _MD_IMAGE.sub("", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    t = _MD_HR.sub("", t)
    t = _MD_LIST.sub("", t)
    t = _MD_QUOTE.sub("", t)
    t = _MD_EMPHASIS.sub(r"\2", t)
    t = _URL.sub("", t)
    # 英単語・略語 (記号掃除より先: Wi-Fi 等のハイフン付き表層を保つ)
    t = _word_regex().sub(_word_repl, t)
    t = _ABBREV_DOT.sub(r"\1", t)
    # 矢印は読点へ (絵文字除去が U+2190-21FF を含むため先に変換する)
    t = _ARROW.sub("、", t)
    t = _MULTIPLY.sub("かける", t)  # ✕✖ (U+2715/2716) も絵文字除去より先に読み替える
    # 絵文字・ネットスラング
    t = _LAUGH_MARK.sub("", t)
    t = _W_LAUGH.sub("", t)
    t = _EMOJI.sub("", t)
    # 数字まわり (カンマ → 通貨 → 日付 → 電話番号 → 時刻 → 範囲 → 単位 の順)
    t = _GROUPED_COMMA.sub("", t)
    t = _CURRENCY_YEN.sub(r"\1円", t)
    t = _CURRENCY_DOLLAR.sub(r"\1ドル", t)
    t = _DATE_YMD.sub(_date_repl, t)
    t = _DATE_MD.sub(_date_md_repl, t)
    t = _PHONE.sub(_phone_repl, t)
    t = _TIME.sub(_time_repl, t)
    t = _RANGE_WAVE.sub("から", t)
    t = _RANGE_HYPHEN.sub(r"\1から\2", t)
    t = _UNIT.sub(_unit_repl, t)
    # 数式・記号
    t = _POWER.sub(r"の\1乗", t)
    t = _EQUALS.sub("イコール", t)
    t = _SILENT_SYMBOLS.sub(" ", t)
    # 空白整理
    t = _NEWLINES.sub(_newline_repl, t)
    t = _SPACES.sub(" ", t)
    return t.strip()
