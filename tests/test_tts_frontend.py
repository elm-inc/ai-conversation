"""日本語 TTS フロントエンド (aiconv.frontend, L0+L1) の回帰テスト。

- L0 (normalize) は依存なしの純粋関数なので常に実行。
- L1 (G2P/アクセント/ユーザー辞書) は pyopenjtalk が無い環境では skip
  (tests/test_tts_bench.py と同じパターン)。導入: uv sync --inexact --extra frontend

ここの期待読みは apps/tts-bench/test_sentences.py の expected_reading と同じ値
(辞書 PR・正規化規則の変更で読みが崩れたら CI で検出する — 設計 §8 読み回帰テスト)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aiconv.frontend import (
    frontend_available,
    mora_count_kana,
    normalize,
    register_words,
)

# ---------------------------------------------------------------------------
# L0: テキスト正規化 (依存なし・常に実行)
# ---------------------------------------------------------------------------


def test_normalize_grouped_number() -> None:
    # 桁区切りカンマ除去 (3,000 → 3000)。読点のカンマ相当は壊さない
    assert normalize("この服、3,000円もしたんだよ。") == "この服、3000円もしたんだよ。"
    assert normalize("1,234,567円") == "1234567円"
    assert normalize("1,2回目") == "1,2回目"  # 列挙のカンマは桁区切りではない


def test_normalize_time() -> None:
    assert normalize("待ち合わせは14:30だから遅れないでね。") == (
        "待ち合わせは14時30分だから遅れないでね。"
    )
    assert normalize("9:05に出る。") == "9時5分に出る。"
    assert normalize("10:00開始。") == "10時開始。"  # 0 分は読まない
    assert normalize("12:34:56に記録。") == "12時34分56秒に記録。"
    assert normalize("スコアは103:99だ。") == "スコアは103 99だ。"  # 時刻ではない (記号掃除のみ)


def test_normalize_english_words() -> None:
    assert normalize("最近、AIとかOpenAIのニュースばっかりだね。") == (
        "最近、エーアイとかオープンエーアイのニュースばっかりだね。"
    )
    # 表層の途中では置換しない (OpenAI の AI / AIDS の AI)
    assert "オープンエーアイ" in normalize("openaiの発表。")  # 大文字小文字は不問
    assert normalize("AIDSの研究。") == "AIDSの研究。"


def test_normalize_register_words() -> None:
    register_words({"Pipecat": "パイプキャット"})
    assert normalize("Pipecatで実装した。") == "パイプキャットで実装した。"


def test_normalize_markdown_and_code() -> None:
    assert normalize("これは**大事**な話。") == "これは大事な話。"
    assert normalize("```python\nprint('hi')\n```実行してみて。") == "実行してみて。"
    assert normalize("コマンドは`ls`だよ。") == "コマンドはlsだよ。"
    assert normalize("# 見出し\n- 項目1\n- 項目2") == "見出し。項目1。項目2"
    assert normalize("[ここ](https://example.com)を見て。") == "ここを見て。"
    assert normalize("詳細は https://example.com/a?b=c を見て。") == "詳細は を見て。"


def test_normalize_emoji_and_slang() -> None:
    assert normalize("やったー！優勝だ🎉 すごくない？") == "やったー!優勝だ すごくない?"
    assert normalize("最高👍👍👍だね") == "最高だね"
    assert normalize("それな(笑)ウケるww") == "それなウケる"


def test_normalize_abbrev_period_protection() -> None:
    # 英略語のピリオドが文境界に誤認されない (A.1 → A1, Mr. → ミスター)
    assert normalize("A.1を参照。") == "A1を参照。"
    assert normalize("Mr.スミスが来た。") == "ミスタースミスが来た。"


def test_normalize_date_and_range() -> None:
    assert normalize("2026/06/11に会おう。") == "2026年6月11日に会おう。"
    assert normalize("2026-06-11締切。") == "2026年6月11日締切。"
    assert normalize("13/45は変換しない。") == "13/45は変換しない。"  # 月日として不正
    assert normalize("3〜4人で行く。") == "3から4人で行く。"
    assert normalize("3-4人で行く。") == "3から4人で行く。"


def test_normalize_md_date() -> None:
    # 単独 M/D 日付 (日>10 なら分数より日付が圧倒的に多い)
    assert normalize("来週の6/11に会おうよ。") == "来週の6月11日に会おうよ。"
    assert normalize("12/5は発売日。") == "12月5日は発売日。"  # 月>日 は分数として不自然
    # 分数と曖昧な領域 (月<日 かつ 日≤10) は安全側で未変換 (docstring に記録)
    assert normalize("ケーキを1/2に切る。") == "ケーキを1/2に切る。"
    assert normalize("6/8はどうかな。") == "6/8はどうかな。"
    # ゼロ埋め・曜日付きは日付確定
    assert normalize("06/08締切。") == "6月8日締切。"
    assert normalize("6/8(土)に集合ね。") == "6月8日土曜日に集合ね。"
    # YYYY/MM/DD の内部 (06/11) を二重変換しない
    assert normalize("2026/06/11に会おう。") == "2026年6月11日に会おう。"


def test_normalize_multiply() -> None:
    assert normalize("3×4は12だよ。") == "3かける4は12だよ。"
    assert normalize("3 * 4も12。") == "3かける4も12。"  # 従来は * を掃除して無音だった
    assert normalize("2✕3と2✖3。") == "2かける3と2かける3。"  # 絵文字除去より先に読み替え
    assert normalize("*強調*は読まない。") == "強調は読まない。"  # 数字に挟まれない * は従来通り


def test_normalize_phone_number() -> None:
    assert normalize("電話番号は03-1234-5678です。") == (
        "電話番号はゼロサンのイチニーサンヨンのゴーロクナナハチです。"
    )
    assert normalize("090-1234-5678にかけて。") == (
        "ゼロキューゼロのイチニーサンヨンのゴーロクナナハチにかけて。"
    )
    assert normalize("0120-444-444は無料。") == (
        "ゼロイチニーゼロのヨンヨンヨンのヨンヨンヨンは無料。"
    )
    # 先頭 0 なし・2 区切りでないものは電話番号と確信できないため未変換 (docstring に記録)
    assert normalize("1234-5678は変換しない。") == "1234-5678は変換しない。"
    assert normalize("3-4人で行く。") == "3から4人で行く。"  # 範囲読みは壊さない


def test_normalize_known_abbreviations() -> None:
    # 英略語の追加分 (SNS/URL/iPhone/Google/Netflix/API)
    assert normalize("SNSでiPhoneの新作URLが流れてきた。") == (
        "エスエヌエスでアイフォンの新作ユーアールエルが流れてきた。"
    )
    assert normalize("GoogleとNetflixのAPI。") == "グーグルとネットフリックスのエーピーアイ。"


def test_normalize_units_currency_math() -> None:
    assert normalize("気温は25℃だ。") == "気温は25度だ。"  # NFKC の ℃→°C 折りたたみも吸収
    assert normalize("あと5km走って2L飲んだ。") == "あと5キロメートル走って2リットル飲んだ。"
    assert normalize("¥3,000のセール。") == "3000円のセール。"
    assert normalize("E=mc^2だ。") == "Eイコールmcの2乗だ。"
    assert normalize("A -> Bへ進む。") == "A 、 Bへ進む。"


def test_normalize_fullwidth_and_newlines() -> None:
    assert normalize("１４：３０に３，０００円") == "14時30分に3000円"
    assert normalize("こんにちは\nところで明日の話。") == "こんにちは。ところで明日の話。"
    assert normalize("おはよう。\nいい天気だね。") == "おはよう。いい天気だね。"


# ---------------------------------------------------------------------------
# L1: G2P / アクセント / ユーザー辞書 (pyopenjtalk が無い環境では skip)
# ---------------------------------------------------------------------------

requires_pyopenjtalk = pytest.mark.skipif(
    frontend_available() is not None, reason=str(frontend_available())
)


def _reading(text: str) -> str:
    from aiconv.frontend import predict_accent, predicted_reading

    return predicted_reading(predict_accent(normalize(text)))


@requires_pyopenjtalk
def test_reading_normalization_cases() -> None:
    """P0 で NG だった正規化ケースが L0 適用で期待読みになる (test_sentences と同値)。"""
    assert _reading("この服、3,000円もしたんだよ。") == "コノフクサンゼンエンモシタンダヨ"
    assert _reading("待ち合わせは14:30だから遅れないでね。") == (
        "マチアワセワジューヨジサンジュップンダカラオクレナイデネ"
    )
    assert _reading("最近、AIとかOpenAIのニュースばっかりだね。") == (
        "サイキンエーアイトカオープンエーアイノニュースバッカリダネ"
    )
    assert _reading("やったー！優勝だ🎉 すごくない？") == "ヤッターユーショーダスゴクナイ"


@requires_pyopenjtalk
def test_user_dict_proper_noun() -> None:
    """ユーザー辞書 (data/accent_dict) で固有名詞の読み + アクセントが固定される。"""
    from aiconv.frontend import load_user_dict, predict_accent

    assert load_user_dict(), "data/accent_dict/*.csv が見つかること"
    assert _reading("宮崎駿のジブリ作品が好きなんだ。") == (
        "ミヤザキハヤオノジブリサクヒンガスキナンダ"
    )
    # アクセント核も辞書値 (5/7) で固定: 宮崎駿が → ミヤザキハ↓ヤオガ (8 モーラ, 核 5)
    first = predict_accent("宮崎駿が好きだ。")[0]
    assert first.reading == "ミヤザキハヤオガ"
    assert first.accent == 5
    assert first.mora_count == 8


@requires_pyopenjtalk
def test_user_dict_character_names() -> None:
    """キャラ名 (あいちゃん/ゆうくん) の読みとアクセントが辞書で固定される。"""
    from aiconv.frontend import predict_accent

    ai = predict_accent("あいちゃんは元気？")[0]
    assert ai.reading == "アイチャンワ"
    assert ai.accent == 1  # あい = ア↓イ (頭高)
    yuu = predict_accent("ゆうくんが来た。")[0]
    assert yuu.reading == "ユークンガ"
    assert yuu.accent == 0  # ゆう = ユー (平板)


@requires_pyopenjtalk
def test_minimal_pair_accent_preserved() -> None:
    """正規化を通しても最小対立 (雨[1]/飴[0]) のアクセントが保たれる。"""
    from aiconv.frontend import predict_accent

    rain = predict_accent(normalize("雨が降る。"))
    candy = predict_accent(normalize("飴が降る。"))
    assert rain[0].reading == candy[0].reading == "アメガ"
    assert rain[0].accent == 1
    assert candy[0].accent == 0


@requires_pyopenjtalk
def test_normalize_and_g2p_integration() -> None:
    """統合 API: text → 正規化 → アクセント句 + 音素列。"""
    from aiconv.frontend import normalize_and_g2p

    r = normalize_and_g2p("この服、3,000円もしたんだよ。")
    assert r.text == "この服、3000円もしたんだよ。"
    assert r.reading == "コノフクサンゼンエンモシタンダヨ"
    assert r.phrases and all(p.mora_count > 0 for p in r.phrases)
    assert "s" in r.phonemes and "sil" not in r.phonemes
    assert "[" in r.fmt_phrases()  # "コノフク[2] ..." 形式


@requires_pyopenjtalk
def test_marine_graceful_fallback() -> None:
    """marine 未導入でも run_marine=True が落ちない (規則ベースへフォールバック)。"""
    import importlib.util

    from aiconv.frontend import predict_accent

    if importlib.util.find_spec("marine") is not None:
        pytest.skip("marine 導入済み環境ではフォールバックは発生しない")
    with pytest.warns(UserWarning, match="marine"):
        phrases = predict_accent("雨が降る。", run_marine=True)
    assert phrases  # 落ちずに規則ベースの結果が返る


def test_mora_count() -> None:
    assert mora_count_kana("ミヤザキハヤオ") == 7
    assert mora_count_kana("チャットジーピーティー") == 9  # 拗音/長音の扱い


# ---------------------------------------------------------------------------
# dict_sync: テーマ keyterms → アクセント辞書候補 (auto_pending.csv, needs-review)
# ---------------------------------------------------------------------------

# 既存 curated 辞書相当 (重複排除の検証用に 宮崎駿 を含める)
_CURATED_ROW = (
    "宮崎駿,,,8609,名詞,固有名詞,人名,一般,*,*,宮崎駿,ミヤザキハヤオ,ミヤザキハヤオ,5/7,*"
)


def test_parse_keyterms() -> None:
    from aiconv.frontend.dict_sync import parse_keyterms

    # bot.py spec.keyterms 形式 (カンマ区切り文字列) とリストの両対応・順序保持・重複排除
    assert parse_keyterms("宮崎駿, スタジオジブリ ,宮崎駿,") == ["宮崎駿", "スタジオジブリ"]
    assert parse_keyterms(["大谷翔平", " 大谷翔平 ", ""]) == ["大谷翔平"]


def test_dict_candidate_csv_row() -> None:
    from aiconv.frontend.dict_sync import DictCandidate

    row = DictCandidate(
        surface="大谷翔平", reading="オオタニショウヘイ", pron="オータニショーヘー",
        accent=5, mora_count=8,
    ).csv_row()
    cols = row.split(",")
    assert len(cols) == 15, "NAIST-JDIC 15 カラム (project_words.csv へそのまま昇格できる形)"
    assert cols[0] == cols[10] == "大谷翔平"  # 表層形 = 原形
    assert cols[4:8] == ["名詞", "固有名詞", "一般", "*"]
    assert cols[11] == "オオタニショウヘイ" and cols[12] == "オータニショーヘー"
    assert cols[13] == "5/8"  # アクセント核/モーラ数


@requires_pyopenjtalk
def test_candidate_for_combined_accent() -> None:
    """複数アクセント句 (姓+名) は結合モーラ位置の最初の非平板核に換算される。"""
    from aiconv.frontend.dict_sync import candidate_for

    c = candidate_for("大谷翔平")  # 素通し: オータニ[0](4) + ショーヘー[1](4)
    assert c.surface == "大谷翔平"
    assert c.reading == "オオタニショウヘイ"  # 読み (カナ綴り)
    assert c.pron == "オータニショーヘー"  # 発音 (長音表記)
    assert c.mora_count == 8
    assert c.accent == 4 + 1  # 後句の核 1 を結合位置 5 に換算


@requires_pyopenjtalk
def test_candidate_for_rejects_non_dictionary_terms() -> None:
    from aiconv.frontend.dict_sync import candidate_for

    with pytest.raises(ValueError, match="KNOWN_WORDS"):
        candidate_for("GPU")  # 英字表層は L0 担当 (MeCab 辞書は ASCII が不安定)
    with pytest.raises(ValueError, match="空"):
        candidate_for("🎉")  # L0 正規化で消える
    # L0 既知語はカタカナ化された表層で候補になる (実行時も L0 正規化後に L1 へ渡るため)
    assert candidate_for("OpenAI").surface == "オープンエーアイ"


@requires_pyopenjtalk
def test_sync_keyterms_appends_and_dedupes(tmp_path: Path) -> None:
    """新規候補のみ auto_pending.csv へ追記し、再実行は冪等 (受け入れ条件 1)。"""
    from aiconv.frontend import PENDING_DICT_FILENAME, sync_keyterms  # 遅延 export 経由

    dict_dir = tmp_path
    (dict_dir / "project_words.csv").write_text(_CURATED_ROW + "\n", encoding="utf-8")

    result = sync_keyterms("宮崎駿, スタジオジブリ, 大谷翔平, GPU", dict_dir=dict_dir)
    assert [c.surface for c in result.added] == ["スタジオジブリ", "大谷翔平"]
    assert result.skipped_existing == ("宮崎駿",)  # curated 辞書と表層重複
    assert [t for t, _ in result.skipped_invalid] == ["GPU"]
    pending = dict_dir / PENDING_DICT_FILENAME
    assert result.pending_path == pending
    lines = pending.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(len(line.split(",")) == 15 for line in lines)

    # 再実行 → 全てスキップ (冪等)。pending 内の表層とも重複排除される
    again = sync_keyterms(["スタジオジブリ", "大谷翔平"], dict_dir=dict_dir)
    assert again.added == ()
    assert set(again.skipped_existing) == {"スタジオジブリ", "大谷翔平"}
    assert pending.read_text(encoding="utf-8").splitlines() == lines


@requires_pyopenjtalk
def test_pending_csv_not_loaded_as_user_dict(tmp_path: Path) -> None:
    """auto_pending.csv (needs-review) は load_user_dict のロード対象外 (隔離)。"""
    from aiconv.frontend import PENDING_DICT_FILENAME, load_user_dict, reset_user_dict

    dict_dir = tmp_path
    (dict_dir / "project_words.csv").write_text(_CURATED_ROW + "\n", encoding="utf-8")
    wrong = "大谷翔平,,,1,名詞,固有名詞,一般,*,*,*,大谷翔平,ダメヨミ,ダメヨミ,0/4,*"
    (dict_dir / PENDING_DICT_FILENAME).write_text(wrong + "\n", encoding="utf-8")
    try:
        applied = load_user_dict(dict_dir)
        assert [p.name for p in applied] == ["project_words.csv"]  # pending は適用されない
        assert _reading("大谷翔平が打った。").startswith("オータニショーヘー")
    finally:
        reset_user_dict()  # 後続テストは既定辞書 (data/accent_dict) を遅延再適用する
