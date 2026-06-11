"""代表文テストセット (アクセント根治の評価用)。

設計 japanese-tts-optimization §P0 のカテゴリを網羅する:
- minimal_pair: アクセント核で意味が変わる最小対立 (橋/箸/端、雨/飴、牡蠣/柿、今/居間)
- proper_noun: 固有名詞・作品名・人名 (読み崩れしやすい)
- character:   プロジェクトのキャラ名「あい」「ゆう」を含む会話文
- long_phrase: アクセント句が複数連なる長め文
- normalization: L0 正規化ケース (数字 / 時刻 / 年 / 英単語 / 記号・絵文字)

表記規約:
- expected_reading は NJD pron 形式の音表記カタカナ (長音は「ー」: 映画=エーガ、
  昨日=キノー)。文全体を空白なしで連結。
- expected_accent は accent_check.parse_expected の形式 "アメガ[1] フルラシイヨ[1]"
  (空白区切りのアクセント句、[n] = 核位置モーラ番号、0 = 平板)。

⚠ expected_accent の初期値は pyopenjtalk-plus (NAIST-JDIC) の予測をベースに、
  NHK アクセント辞典の語アクセントと照らして妥当なものを採用した。**人手 (音声) 検証は
  未了**。修正する場合はコメントに根拠を残すこと。proper_noun / normalization カテゴリは
  読み崩れ自体が論点のため expected_accent は設定せず、expected_reading を正解とする
  (現状の pyopenjtalk 素通しでは fail するものを含む = L0 正規化が必要な根拠)。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    MINIMAL_PAIR = "minimal_pair"
    PROPER_NOUN = "proper_noun"
    CHARACTER = "character"
    LONG_PHRASE = "long_phrase"
    NORMALIZATION = "normalization"


@dataclass(frozen=True, slots=True)
class TestSentence:
    id: str
    text: str
    category: Category
    expected_reading: str | None = None  # 文全体の読み (音表記カタカナ、任意)
    expected_accent: str | None = None  # アクセント句列 "読み[核位置] ..." (任意)
    note: str = ""


SENTENCES: tuple[TestSentence, ...] = (
    # --- 最小対立ペア ---
    TestSentence(
        id="mp-ame-rain",
        text="明日は雨が降るらしいよ。",
        category=Category.MINIMAL_PAIR,
        expected_reading="アシタワアメガフルラシイヨ",
        expected_accent="アシタワ[3] アメガ[1] フルラシイヨ[1]",
        note="雨=ア↓メ(頭高1)。飴(平板0)と最小対立。明日=アシタ↓(尾高3)",
    ),
    TestSentence(
        id="mp-ame-candy",
        text="この飴がすごく甘いんだ。",
        category=Category.MINIMAL_PAIR,
        expected_reading="コノアメガスゴクアマインダ",
        expected_accent="コノ[0] アメガ[0] スゴク[2] アマインダ[0]",
        note="飴=アメ(平板0)が核心。甘いんだ[0]は平板化容認 (アマ↓イ[2]起源)",
    ),
    TestSentence(
        id="mp-hashi-bridge",
        text="箸を持って橋を渡る。",
        category=Category.MINIMAL_PAIR,
        expected_reading="ハシヲモッテハシヲワタル",
        expected_accent="ハシヲ[1] モッテ[1] ハシヲ[2] ワタル[0]",
        note="箸=ハ↓シ(1) / 橋=ハシ↓(尾高2→ヲで核実現)。1文に両方入れて差を見る",
    ),
    TestSentence(
        id="mp-hashi-edge",
        text="机の端に置いといて。",
        category=Category.MINIMAL_PAIR,
        expected_reading="ツクエノハシニオイトイテ",
        expected_accent="ツクエノ[0] ハシニ[0] オイトイテ[3]",
        note="端=ハシ(平板0)。橋/箸と最小対立",
    ),
    TestSentence(
        id="mp-kaki",
        text="冬は牡蠣で、秋は柿が美味しいね。",
        category=Category.MINIMAL_PAIR,
        expected_reading="フユワカキデアキワカキガオイシイネ",
        expected_accent="フユワ[2] カキデ[1] アキワ[1] カキガ[0] オイシイネ[3]",
        note="牡蠣=カ↓キ(1) / 柿=カキ(平板0)",
    ),
    TestSentence(
        id="mp-ima",
        text="今、居間でテレビを見てる。",
        category=Category.MINIMAL_PAIR,
        expected_reading="イマイマデテレビヲミテル",
        expected_accent="イマ[1] イマデ[2] テレビヲ[1] ミテル[1]",
        note="今=イ↓マ(1) / 居間=イマ↓(尾高2)",
    ),
    # --- 固有名詞・作品名・人名 (読み崩れ検出。expected_reading が正解) ---
    TestSentence(
        id="pn-shinkai",
        text="新海誠の映画を渋谷で観たよ。",
        category=Category.PROPER_NOUN,
        expected_reading="シンカイマコトノエーガヲシブヤデミタヨ",
        note="人名 (誠=マコト)。アクセントは人手確認待ちのため未設定",
    ),
    TestSentence(
        id="pn-ghibli",
        text="宮崎駿のジブリ作品が好きなんだ。",
        category=Category.PROPER_NOUN,
        expected_reading="ミヤザキハヤオノジブリサクヒンガスキナンダ",
        note="駿=ハヤオ。pyopenjtalk 素通しはシュンに崩れる (既知 fail、辞書整備の根拠)",
    ),
    TestSentence(
        id="pn-otani",
        text="大谷翔平がホームランを打った。",
        category=Category.PROPER_NOUN,
        expected_reading="オータニショーヘーガホームランヲウッタ",
        note="人名+外来語",
    ),
    # --- キャラ名 (あい/ゆう) を含む会話文 ---
    TestSentence(
        id="ch-ai",
        text="あいちゃんは最近どんな音楽聴いてるの？",
        category=Category.CHARACTER,
        expected_reading="アイチャンワサイキンドンナオンガクキイテルノ",
        expected_accent="アイチャンワ[1] サイキン[0] ドンナ[1] オンガク[1] キイテルノ[4]",
        note="キャラ名「あい」=ア↓イ(頭高)。疑問文末尾の上昇はアクセント表記外",
    ),
    TestSentence(
        id="ch-yuu",
        text="ゆうくんが昨日話してた映画、結局観たの？",
        category=Category.CHARACTER,
        expected_reading="ユークンガキノーハナシテタエーガケッキョクミタノ",
        expected_accent="ユークンガ[0] キノー[2] ハナシテタ[2] エーガ[0] ケッキョク[0] ミタノ[1]",
        note="キャラ名「ゆう」=ユー(平板)。映画=エーガ(平板)",
    ),
    # --- アクセント句が連なる長め文 ---
    TestSentence(
        id="lp-tocho",
        text="東京都庁の展望台から富士山がきれいに見えたんだよ。",
        category=Category.LONG_PHRASE,
        expected_reading="トーキョートチョーノテンボーダイカラフジサンガキレイニミエタンダヨ",
        expected_accent=(
            "トーキョートチョーノ[5] テンボーダイカラ[3] フジサンガ[1] キレイニ[1] ミエタンダヨ[1]"
        ),
        note="複合語 (東京都庁=トーキョート↓チョー) の句のまとまり",
    ),
    TestSentence(
        id="lp-denwa",
        text="昨日の夜遅くまで友達と電話で話してたから、ちょっと眠いんだよね。",
        category=Category.LONG_PHRASE,
        expected_reading=(
            "キノーノヨルオソクマデトモダチトデンワデハナシテタカラチョットネムインダヨネ"
        ),
        expected_accent=(
            "キノーノ[2] ヨル[1] オソクマデ[2] トモダチト[0] デンワデ[0] "
            "ハナシテタカラ[2] チョット[1] ネムインダヨネ[5]"
        ),
        note="会話調の長文。ネムインダヨネ[5] は要人手確認 (ネム↓イ[2]起源なら[2]が自然か)",
    ),
    # --- L0 正規化ケース (読みが正解。現状 fail を含む = 正規化レイヤが必要な根拠) ---
    TestSentence(
        id="nm-price",
        text="この服、3,000円もしたんだよ。",
        category=Category.NORMALIZATION,
        expected_reading="コノフクサンゼンエンモシタンダヨ",
        note="桁区切りカンマ。pyopenjtalk 素通しは「サン、ゼロゼロゼロエン」に崩れる (既知 fail)",
    ),
    TestSentence(
        id="nm-time",
        text="待ち合わせは14:30だから遅れないでね。",
        category=Category.NORMALIZATION,
        expected_reading="マチアワセワジューヨジサンジュップンダカラオクレナイデネ",
        note="時刻表記 (コロン)。素通しは「ジューヨン サンジュー」に崩れる (既知 fail)",
    ),
    TestSentence(
        id="nm-year",
        text="2025年の夏は本当に暑かったね。",
        category=Category.NORMALIZATION,
        expected_reading="ニセンニジューゴネンノナツワホントーニアツカッタネ",
        note="西暦年の読み (これは素通しで正しく読める)",
    ),
    TestSentence(
        id="nm-english",
        text="最近、AIとかOpenAIのニュースばっかりだね。",
        category=Category.NORMALIZATION,
        expected_reading="サイキンエーアイトカオープンエーアイノニュースバッカリダネ",
        note="英字略語・英単語。素通しは OpenAI を1字ずつ読む (既知 fail)",
    ),
    TestSentence(
        id="nm-emoji",
        text="やったー！優勝だ🎉 すごくない？",
        category=Category.NORMALIZATION,
        expected_reading="ヤッターユーショーダスゴクナイ",
        note="感嘆符・絵文字。絵文字は読まない (無視) が正",
    ),
)
