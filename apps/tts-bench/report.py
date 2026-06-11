"""ベンチ結果の CSV / Markdown レポート出力。"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from accent_check import AccentCheck
from test_sentences import TestSentence


@dataclass(frozen=True, slots=True)
class BenchRow:
    """1 エンジン × 1 文の計測行。"""

    engine: str
    sentence_id: str
    category: str
    text: str
    ok: bool
    error: str = ""
    ttfa_ms: float | None = None
    elapsed_ms: float | None = None
    duration_s: float | None = None
    rtf: float | None = None
    vram_peak_mb: float | None = None
    streaming: bool | None = None
    wav_path: str = ""
    notes: str = ""


def write_csv(rows: list[BenchRow], path: Path) -> None:
    cols = [f.name for f in fields(BenchRow)]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([getattr(r, c) for c in cols])


def _fmt(v: float | None, spec: str = ".0f") -> str:
    return format(v, spec) if v is not None else "-"


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def _engine_summary_lines(rows: list[BenchRow]) -> list[str]:
    lines = [
        "| エンジン | 成功 | TTFA 中央値 (ms) | RTF 中央値 | VRAM peak (MB) | 備考 |",
        "|---|---|---|---|---|---|",
    ]
    for engine in sorted({r.engine for r in rows}):
        ers = [r for r in rows if r.engine == engine]
        oks = [r for r in ers if r.ok]
        ttfa = _median([r.ttfa_ms for r in oks if r.ttfa_ms is not None])
        rtf_m = _median([r.rtf for r in oks if r.rtf is not None])
        vrams = [r.vram_peak_mb for r in oks if r.vram_peak_mb is not None]
        streaming = all(r.streaming for r in oks) if oks else None
        note = "" if streaming else "非ストリーミング (TTFA=全合成時間)"
        lines.append(
            f"| {engine} | {len(oks)}/{len(ers)} | {_fmt(ttfa)} | {_fmt(rtf_m, '.2f')} | "
            f"{_fmt(max(vrams) if vrams else None)} | {note} |"
        )
    return lines


def _accent_section_lines(
    accent_results: list[AccentCheck], skip_reason: str | None
) -> list[str]:
    lines = ["## アクセントチェック (フロントエンド層・エンジン非依存)", ""]
    if skip_reason is not None:
        lines += [f"スキップ: {skip_reason}", ""]
        return lines
    rated = [r.phrase_match_rate for r in accent_results if r.phrase_match_rate is not None]
    readings = [r.reading_ok for r in accent_results if r.reading_ok is not None]
    if rated:
        lines.append(
            f"- アクセント句一致率 (expected_accent あり {len(rated)} 文の平均): "
            f"**{statistics.mean(rated):.0%}**"
        )
    if readings:
        lines.append(
            f"- 読み一致 (expected_reading あり {len(readings)} 文): "
            f"**{sum(readings)}/{len(readings)}**"
        )
    lines += [
        "",
        "| id | 予測 (読み[核位置], 0=平板) | 句一致 | 読み | 差分 |",
        "|---|---|---|---|---|",
    ]
    for r in accent_results:
        rate = f"{r.phrase_match_rate:.0%}" if r.phrase_match_rate is not None else "-"
        rd = {True: "OK", False: "**NG**", None: "-"}[r.reading_ok]
        detail = r.detail.replace("|", "\\|")
        lines.append(f"| {r.sentence_id} | {r.predicted} | {rate} | {rd} | {detail} |")
    return lines


def _judge_section_lines(judge_result: dict[str, Any]) -> list[str]:
    lines = ["## LLM 読みサニティ (--judge)", ""]
    summary = judge_result.get("summary", "")
    if summary:
        lines += [f"総評: {summary}", ""]
    lines += ["| id | 読み | アクセント | 指摘 |", "|---|---|---|---|"]
    for it in judge_result.get("items", []):
        rd = "OK" if it.get("reading_ok") else "**NG**"
        ac = "OK" if it.get("accent_ok") else "**NG**"
        comment = str(it.get("comment", "")).replace("|", "\\|")
        lines.append(f"| {it.get('id')} | {rd} | {ac} | {comment} |")
    return lines


def _listening_sheet_lines(rows: list[BenchRow]) -> list[str]:
    lines = [
        "## 聴取シート (人手採点フック)",
        "",
        "各 wav を聴いて 1-5 (5=最良) を記入する。アクセント=高低の自然さ "
        "(平板読み・核ズレがないか)。",
        "",
        "| id | エンジン | wav | 自然さ | アクセント | 明瞭さ | メモ |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r.sentence_id, r.engine)):
        if r.ok:
            lines.append(f"| {r.sentence_id} | {r.engine} | {r.wav_path} |  |  |  |  |")
    return lines


def write_report(
    path: Path,
    *,
    sentences: list[TestSentence],
    rows: list[BenchRow],
    accent_results: list[AccentCheck],
    accent_skip_reason: str | None,
    engine_status: dict[str, str],
    setup_s: dict[str, float],
    judge_result: dict[str, Any] | None,
) -> None:
    lines = [
        "# 日本語 TTS エンジン比較レポート",
        "",
        f"- 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- テスト文: {len(sentences)} 文 (apps/tts-bench/test_sentences.py)",
        "- レイテンシ目標: TTFA < 300ms (発話終了→最初の音)",
        "",
        "## エンジン状態",
        "",
        "| エンジン | 状態 | 初期化+ウォームアップ (s) |",
        "|---|---|---|",
    ]
    for name, status in engine_status.items():
        st = "利用可" if status == "ok" else status.replace("|", "\\|")
        lines.append(f"| {name} | {st} | {_fmt(setup_s.get(name), '.1f')} |")
    lines.append("")

    if rows:
        lines += ["## エンジン比較サマリ", ""]
        lines += _engine_summary_lines(rows)
        lines += [
            "",
            "注: VRAM は torch.cuda の peak allocated (同一プロセスのみ。別プロセスの "
            "VOICEVOX ENGINE は対象外)。CUDA 無し環境では `-`。",
            "",
        ]

    lines += _accent_section_lines(accent_results, accent_skip_reason)
    lines.append("")

    if judge_result is not None:
        lines += _judge_section_lines(judge_result)
        lines.append("")

    if rows:
        lines += _listening_sheet_lines(rows)
        lines.append("")
        failed = [r for r in rows if not r.ok]
        if failed:
            lines += ["## 失敗", "", "| エンジン | id | エラー |", "|---|---|---|"]
            for r in failed:
                err = r.error.replace("|", "\\|")
                lines.append(f"| {r.engine} | {r.sentence_id} | {err} |")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
