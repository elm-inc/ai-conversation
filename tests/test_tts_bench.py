"""tts-bench (apps/tts-bench) のスモークテスト。

apps/tts-bench はスクリプト群 (パッケージ化していない) のため sys.path 経由で import する。
pyopenjtalk が無い環境でも壊れない (アクセント予測は skip、dry-run は graceful に
スキップ案内を出して成功する) ことを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCH = Path(__file__).resolve().parents[1] / "apps" / "tts-bench"
sys.path.insert(0, str(_BENCH))

from test_sentences import SENTENCES, Category  # noqa: E402


def test_sentence_set_wellformed() -> None:
    ids = [s.id for s in SENTENCES]
    assert len(ids) == len(set(ids)), "id が重複している"
    assert 12 <= len(SENTENCES) <= 20
    assert {s.category for s in SENTENCES} == set(Category), "全カテゴリを網羅すること"
    for s in SENTENCES:
        assert s.text.strip()


def test_expected_accent_parseable() -> None:
    from accent_check import parse_expected

    n = 0
    for s in SENTENCES:
        if s.expected_accent:
            phrases = parse_expected(s.expected_accent)
            assert phrases
            n += 1
    assert n >= 6, "expected_accent 付きの文が最小対立ペア分は必要"


def test_accent_predict_minimal_pair() -> None:
    pytest.importorskip("pyopenjtalk")
    from aiconv.frontend import predict_accent

    # 雨 (頭高1) / 飴 (平板0) の最小対立がフロントエンドで区別されること
    rain = predict_accent("雨が降る。")
    candy = predict_accent("飴が降る。")
    assert rain[0].reading == candy[0].reading == "アメガ"
    assert rain[0].accent == 1
    assert candy[0].accent == 0


def test_dry_run_smoke(tmp_path: Path) -> None:
    import run_bench

    rc = run_bench.main(["--dry-run", "--out", str(tmp_path)])
    assert rc == 0
    report = tmp_path / "report.md"
    assert report.is_file()
    assert "アクセントチェック" in report.read_text(encoding="utf-8")
