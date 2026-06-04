"""日本語テキストの「発話完結度」スコアリング (ベンダー非依存・pure)。

ターン検出 (FusionTurnDetector) のテキスト側 signal。無音長や音響ヒントと融合して使う。
日本語固有の難所 (設計 §自作モジュール詳細) を素朴なルールで扱う v1:

- 文末助詞 (ね/よ/か/わ/の/さ/ぞ/な…) や文末形 (です/ます/だ/。) → 完結度 高
- 助詞止め (が/を/に/で/と/けど/から/し…) → 継続の合図、完結度 低〜中 (ターン放棄もある)
- フィラー (えーと/あの/その/うーん…) で終わる → まだ続く、完結度 低
- 相槌 (うん/はい/なるほど…) の単独短発話 → BACKCHANNEL 判定に使う

ルールベースだが TurnDetector ポート背後なので、後で学習モデルに差し替え可能。
"""

from __future__ import annotations

# 文末助詞・終助詞的な語尾 (これで終われば完結度が高い)
_FINAL_PARTICLES: tuple[str, ...] = (
    "よね",
    "ですか",
    "ますか",
    "でしょう",
    "だろう",
    "かな",
    "かね",
    "ね",
    "よ",
    "わ",
    "さ",
    "ぞ",
    "な",
    "か",
    "の",
)

# 文末になりやすい述語語尾
_SENTENCE_END_FORMS: tuple[str, ...] = (
    "です",
    "ます",
    "ました",
    "ません",
    "でした",
    "ください",
    "だ",
    "だった",
    "する",
    "した",
    "しない",
    "ない",
    "たい",
    "ある",
    "いる",
    "なる",
    "れる",
)

# 句読点・終端記号
_TERMINALS: tuple[str, ...] = ("。", "．", "！", "？", "!", "?")

# 助詞止め (継続助詞)。これで終わるのは文法的に未完だが、実対話ではターン放棄もある
_CONTINUATIVE_PARTICLES: tuple[str, ...] = (
    "けれども",
    "けれど",
    "けど",
    "ので",
    "から",
    "のに",
    "が",
    "を",
    "に",
    "へ",
    "で",
    "と",
    "や",
    "は",
    "も",
    "し",
    "て",
)

# フィラー (これで終わる/これだけなら、まだ話す)
_FILLERS: tuple[str, ...] = (
    "えーと",
    "えっと",
    "ええと",
    "あの",
    "あのー",
    "その",
    "そのー",
    "うーん",
    "んー",
    "なんか",
    "まあ",
)

# 相槌 (短い単独発話ならターンを奪わない)
_BACKCHANNELS: frozenset[str] = frozenset(
    {
        "うん",
        "うんうん",
        "ううん",
        "はい",
        "ええ",
        "えー",
        "そう",
        "そうそう",
        "そっか",
        "なるほど",
        "へえ",
        "へー",
        "ふーん",
        "ふんふん",
        "おお",
        "おー",
        "まじ",
        "りょうかい",
    }
)

# 割り込み開始によく出る語 (barge-in 兆候)
_INTERRUPTION_MARKERS: tuple[str, ...] = ("いや", "ちょっと", "まって", "でも", "あっ", "ねえ")


def _normalize(text: str) -> str:
    return text.strip().rstrip("、,").strip()


def is_filler_only(text: str) -> bool:
    """発話がフィラーだけで構成されているか (まだ続く)。"""
    t = _normalize(text)
    return bool(t) and t in _FILLERS


def ends_with_filler(text: str) -> bool:
    t = _normalize(text)
    return any(t.endswith(f) for f in _FILLERS)


def is_backchannel(text: str) -> bool:
    """短い単独の相槌か (ターンを奪わない)。"""
    t = _normalize(text)
    return t in _BACKCHANNELS


def looks_like_interruption(text: str) -> bool:
    """割り込み開始語で始まるか (barge-in 兆候)。"""
    t = _normalize(text)
    return any(t.startswith(m) for m in _INTERRUPTION_MARKERS)


def text_completion_score(text: str) -> float:
    """発話完結度を [0.0, 1.0] で返す。高いほど「言い終えた」可能性が高い。"""
    t = _normalize(text)
    if not t:
        return 0.0
    # フィラーで終わる/フィラーのみ → まだ続く
    if is_filler_only(t) or ends_with_filler(t):
        return 0.1
    # 終端記号 → ほぼ確実に完結
    if t.endswith(_TERMINALS):
        return 0.95
    # 文末助詞 → 完結度 高
    if t.endswith(_FINAL_PARTICLES):
        return 0.85
    # 述語語尾 → 完結度 高め
    if t.endswith(_SENTENCE_END_FORMS):
        return 0.8
    # 助詞単独 (「を」「けど」だけ等) → 断片。ノイズ/言いかけ扱いで最低限
    if t in _CONTINUATIVE_PARTICLES:
        return 0.15
    # 助詞止め → 継続の合図 (低め。音響が終端を示せばターン放棄と解釈)
    if t.endswith(_CONTINUATIVE_PARTICLES):
        return 0.35
    # それ以外 (体言止め等は判別困難) → 中間
    return 0.55
