"""日本語 TTS エンジン比較ハーネス (AIC-8 P0) — エントリポイント。

テストセット (test_sentences) × 利用可能エンジンで合成し、TTFA / RTF / VRAM / 音声長を
計測。pyopenjtalk によるアクセント予測チェック (エンジン非依存層) と合わせて
CSV + Markdown レポートを出力する。

エンジンが未導入/未起動でもハーネスは止まらない (graceful degradation):
check() で利用不可と分かったエンジンは理由つきで skip し、残りだけ計測する。

    uv run python apps/tts-bench/run_bench.py --dry-run        # 配線検証 (合成なし)
    uv run python apps/tts-bench/run_bench.py                  # 利用可能な全エンジン
    uv run python apps/tts-bench/run_bench.py --engines elevenlabs,voicevox
    uv run python apps/tts-bench/run_bench.py --judge          # LLM 読みサニティ付き

セットアップ手順・ライセンス注記は apps/tts-bench/README.md 参照。
"""

from __future__ import annotations

import argparse
import time
import wave
from pathlib import Path
from typing import Any

import metrics
from accent_check import AccentCheck, check_sentence
from engines import ENGINE_NAMES, EngineUnavailableError, create_engine
from engines.base import Engine
from report import BenchRow, write_csv, write_report
from test_sentences import SENTENCES, TestSentence

from aiconv.frontend import (
    format_phrases,
    frontend_available,
    normalize,
    predict_accent,
    predicted_reading,
)

_WARMUP_TEXT = "ウォームアップです。"


def _save_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def _run_accent_check(sentences: list[TestSentence]) -> tuple[list[AccentCheck], str | None]:
    reason = frontend_available()
    if reason is not None:
        print(f"[accent] {reason}")
        return [], reason
    results = [check_sentence(s) for s in sentences]
    rated = [r.phrase_match_rate for r in results if r.phrase_match_rate is not None]
    readings = [r.reading_ok for r in results if r.reading_ok is not None]
    for r in results:
        rate = f"{r.phrase_match_rate:.0%}" if r.phrase_match_rate is not None else "  - "
        rd = {True: "OK", False: "NG", None: "- "}[r.reading_ok]
        print(f"[accent] {r.sentence_id:16s} 句一致={rate:5s} 読み={rd} {r.detail}")
    if rated:
        mean = sum(rated) / len(rated)
        print(f"[accent] アクセント句一致率: {mean:.0%} ({len(rated)} 文)")
    if readings:
        print(f"[accent] 読み一致: {sum(readings)}/{len(readings)} 文")
    return results, None


def _bench_engine(
    engine: Engine, sentences: list[TestSentence], out_dir: Path, *, warmup: bool
) -> tuple[list[BenchRow], float, str]:
    """1 エンジンを全テスト文で計測する。戻り値は (計測行, 初期化秒, 状態)。

    計測途中でエンジンが落ちても (EngineUnavailableError) 部分結果は返す。
    """
    t0 = time.perf_counter()
    engine.prepare()
    if warmup:
        engine.synthesize(_WARMUP_TEXT)
    setup_s = time.perf_counter() - t0

    rows: list[BenchRow] = []
    edir = out_dir / engine.name
    edir.mkdir(parents=True, exist_ok=True)
    for s in sentences:
        metrics.vram_reset()
        try:
            # L0 正規化を通して合成する (数字/英単語/記号の読み崩れをエンジン非依存で防ぐ)
            r = engine.synthesize(normalize(s.text))
        except EngineUnavailableError as e:
            # エンジンごと打ち切り (部分結果は保持し、状態に理由を残す)
            print(f"[{engine.name}] 計測中断: {e}")
            return rows, setup_s, f"計測中断 ({s.id} 以降): {e}"
        except Exception as e:  # noqa: BLE001 — 1 文の失敗でハーネスを止めない
            print(f"[{engine.name}] {s.id}: 失敗 {e}")
            rows.append(
                BenchRow(
                    engine=engine.name,
                    sentence_id=s.id,
                    category=str(s.category),
                    text=s.text,
                    ok=False,
                    error=str(e)[:300],
                )
            )
            continue
        wav_path = edir / f"{s.id}.wav"
        _save_wav(wav_path, r.pcm, r.sample_rate)
        rows.append(
            BenchRow(
                engine=engine.name,
                sentence_id=s.id,
                category=str(s.category),
                text=s.text,
                ok=True,
                ttfa_ms=r.ttfa_ms,
                elapsed_ms=r.elapsed_ms,
                duration_s=r.duration_s,
                rtf=metrics.rtf(r.elapsed_ms, r.duration_s),
                vram_peak_mb=metrics.vram_peak_mb(),
                streaming=r.streaming,
                wav_path=str(wav_path),
                notes=r.notes,
            )
        )
        print(
            f"[{engine.name}] {s.id}: TTFA={r.ttfa_ms:.0f}ms "
            f"elapsed={r.elapsed_ms:.0f}ms dur={r.duration_s:.2f}s"
        )
    return rows, setup_s, "ok"


def _run_judge(
    sentences: list[TestSentence], accent_results: list[AccentCheck], model: str
) -> dict[str, Any] | None:
    """LLM による読み/アクセントのサニティチェック (失敗してもハーネスは続行)。"""
    if not accent_results:
        print("[judge] アクセント予測が無いためスキップ (pyopenjtalk が必要)")
        return None
    from judge import judge_readings

    items: list[dict[str, str]] = []
    for s in sentences:
        phrases = predict_accent(normalize(s.text))
        items.append(
            {
                "id": s.id,
                "text": s.text,
                "reading": predicted_reading(phrases),
                "accent": format_phrases(phrases),
            }
        )
    try:
        result = judge_readings(items, model=model)
    except Exception as e:  # noqa: BLE001
        print(f"[judge] 失敗 (スキップして続行): {e}")
        return None
    print(f"[judge] {result.get('summary', '')}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="日本語 TTS エンジン比較ハーネス (AIC-8 P0)")
    ap.add_argument(
        "--engines",
        default=",".join(ENGINE_NAMES),
        help=f"カンマ区切りで対象エンジン (既定: {','.join(ENGINE_NAMES)} のうち利用可能なもの)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="エンジン無しでテストセット読込+アクセントチェックのみ (配線検証)",
    )
    ap.add_argument("--ids", help="カンマ区切りでテスト文 id を絞り込む")
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"), help="出力ディレクトリ")
    ap.add_argument("--judge", action="store_true", help="LLM 読みサニティチェックを実行")
    ap.add_argument("--judge-model", default="claude-sonnet-4-6", help="judge に使う LLM")
    ap.add_argument("--no-warmup", action="store_true", help="計測前のウォームアップ合成を省く")
    args = ap.parse_args(argv)

    sentences = list(SENTENCES)
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        unknown = wanted - {s.id for s in sentences}
        if unknown:
            ap.error(f"未知の id: {', '.join(sorted(unknown))}")
        sentences = [s for s in sentences if s.id in wanted]
    print(f"[bench] テスト文 {len(sentences)} 文")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) アクセントチェック (エンジン非依存・フロントエンド層)
    accent_results, accent_skip = _run_accent_check(sentences)

    # 2) エンジン計測
    rows: list[BenchRow] = []
    engine_status: dict[str, str] = {}
    setup_s: dict[str, float] = {}
    if args.dry_run:
        print("[bench] --dry-run: エンジン合成はスキップ")
    else:
        selected = [x.strip() for x in args.engines.split(",") if x.strip()]
        unknown_engines = set(selected) - set(ENGINE_NAMES)
        if unknown_engines:
            ap.error(f"未知のエンジン: {', '.join(sorted(unknown_engines))}")
        for name in selected:
            engine = create_engine(name)
            reason = engine.check()
            if reason is not None:
                engine_status[name] = reason
                print(f"[{name}] skip: {reason}")
                continue
            try:
                erows, sec, status = _bench_engine(
                    engine, sentences, out_dir, warmup=not args.no_warmup
                )
            except EngineUnavailableError as e:
                engine_status[name] = str(e)
                print(f"[{name}] skip: {e}")
                continue
            except Exception as e:  # noqa: BLE001 — 初期化失敗でも他エンジンは続行
                engine_status[name] = f"初期化失敗: {e}"
                print(f"[{name}] 初期化失敗 (skip): {e}")
                continue
            engine_status[name] = status
            setup_s[name] = sec
            rows.extend(erows)

    # 3) LLM 読みサニティ (任意)
    judge_result = (
        _run_judge(sentences, accent_results, args.judge_model) if args.judge else None
    )

    # 4) レポート出力
    if rows:
        write_csv(rows, out_dir / "results.csv")
        print(f"[bench] CSV: {out_dir / 'results.csv'}")
    write_report(
        out_dir / "report.md",
        sentences=sentences,
        rows=rows,
        accent_results=accent_results,
        accent_skip_reason=accent_skip,
        engine_status=engine_status,
        setup_s=setup_s,
        judge_result=judge_result,
    )
    print(f"[bench] レポート: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
