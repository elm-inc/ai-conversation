"""日本語 TTS フロントエンド (aiconv.frontend, L0+L1) の回帰テスト。

- L0 (normalize) は依存なしの純粋関数なので常に実行。
- L1 (G2P/アクセント/ユーザー辞書) は pyopenjtalk が無い環境では skip
  (tests/test_tts_bench.py と同じパターン)。導入: uv sync --inexact --extra frontend

ここの期待読みは apps/tts-bench/test_sentences.py の expected_reading と同じ値
(辞書 PR・正規化規則の変更で読みが崩れたら CI で検出する — 設計 §8 読み回帰テスト)。
"""

from __future__ import annotations

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
