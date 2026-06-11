"""テーマ keyterms → アクセント辞書候補の自動同期 (設計 japanese-tts-optimization §5-L1, §10-4)。

bot.py `_expand_theme` が生成するテーマ keyterms (固有名詞・作品名・人名・専門語) を
pyopenjtalk で読み・モーラ数・アクセント核の候補に変換し、OpenJTalk ユーザー辞書形式の
CSV 行として `data/accent_dict/auto_pending.csv` へ追記する。既存辞書
(project_words.csv + auto_pending.csv) と表層で重複排除するため再実行は冪等。

**自動生成行はそのまま信用しない (needs-review)**: pyopenjtalk は未知の固有名詞の読みを
誤ることがある (例: 宮崎駿 → ミヤザキ「シュン」、米津玄師 → ヨネツ「ゲンシ」)。
そのため候補は curated な project_words.csv とは別ファイルに置き、
`load_user_dict()` は auto_pending.csv を **ロードしない**。人間が読み・アクセントを
確認 (必要なら修正) して project_words.csv へ昇格して初めて発話に反映される
(昇格手順: data/accent_dict/README.md)。

bot.py への配線 (フォローアップ。本 PR では bot.py の稼働パスを変更しない):
    # apps/voice-agent/bot.py の _expand_theme() が keyterms を得た直後に
    from aiconv.frontend import sync_keyterms
    try:
        sync_keyterms(kt)  # kt = カンマ区切り keyterms 文字列 (spec.keyterms と同形式)
    except Exception:
        logger.warning("dict_sync 失敗 (会話は続行)")  # 辞書同期の失敗で本体を止めない

CLI:
    uv run python -m aiconv.frontend.dict_sync --keyterms "宮崎駿, スタジオジブリ, 大谷翔平"
    uv run python -m aiconv.frontend.dict_sync --keyterms-file keyterms.txt
    (--dict-dir で data/accent_dict 以外へ出力可)

候補生成の仕様・制限:
- 表層は L0 `normalize()` を通した形で登録する (実行時テキストも L0 → L1 の順に流れる
  ため、辞書は正規化後の表層に一致させる)。L0 既知語 (OpenAI 等) はカタカナ化された
  表層で候補になる。
- 英字のみ・空白/カンマ/引用符を含む表層は候補化しない (英単語の読みは L0 の
  KNOWN_WORDS が担当 — MeCab 辞書は ASCII 表層の扱いが不安定。README 参照)。
- 複数アクセント句に分かれる語 (姓+名等) は、結合読みのモーラ位置に換算した最初の
  非平板核を候補アクセントとする (全句平板なら 0)。1 語登録は 1 句に併合されるため、
  核位置の妥当性は昇格時に人間が判断する。
- プロセス内で既にユーザー辞書がグローバル適用されている場合、候補の読みはその影響を
  受けうる (CLI は新規プロセスのため常に素の NAIST-JDIC 予測)。
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .accent import (
    ENV_DICT_DIR,
    PENDING_DICT_FILENAME,
    AccentPhrase,
    find_dict_dir,
    frontend_available,
    norm_kana,
    predict_accent,
)
from .text_normalize import normalize

# 固有名詞のコスト目安 (data/accent_dict/README.md。小さいほど採用されやすい)
_DEFAULT_COST = 8609

# 日本語文字 (かな・カナ・長音・漢字・々) を 1 文字以上含む表層だけ候補化する
_JA_CHAR = re.compile(r"[ぁ-ゖァ-ヺー一-鿿々]")
# MeCab CSV を壊す文字 (カンマ・引用符・改行) と空白 (表層の途中一致を不安定にする)
_BAD_SURFACE = re.compile(r"[,\"\n\r\s]")


@dataclass(frozen=True, slots=True)
class DictCandidate:
    """auto_pending.csv の 1 行ぶんの辞書候補 (needs-review)。"""

    surface: str  # 表層形 (L0 正規化後)
    reading: str  # 読み (カナ綴り: NJD read 連結。オオタニショウヘイ)
    pron: str  # 発音 (長音表記: NJD pron 連結。オータニショーヘー)
    accent: int  # 推定アクセント核位置 (0=平板)
    mora_count: int

    def csv_row(self) -> str:
        """OpenJTalk ユーザー辞書 CSV (NAIST-JDIC 15 カラム、品詞=名詞,固有名詞,一般)。"""
        return (
            f"{self.surface},,,{_DEFAULT_COST},名詞,固有名詞,一般,*,*,*,"
            f"{self.surface},{self.reading},{self.pron},{self.accent}/{self.mora_count},*"
        )


@dataclass(frozen=True, slots=True)
class SyncResult:
    """sync_keyterms の結果 (CLI 表示・テスト用)。"""

    added: tuple[DictCandidate, ...]  # auto_pending.csv へ追記した候補
    skipped_existing: tuple[str, ...]  # 既存辞書 (curated + pending) に表層があった語
    skipped_invalid: tuple[tuple[str, str], ...]  # (語, 候補化しない理由)
    pending_path: Path  # 追記先 (追加 0 件でもパスは返す)


def parse_keyterms(keyterms: str | Sequence[str]) -> list[str]:
    """カンマ区切り文字列 (bot.py spec.keyterms 形式) or リスト → 重複排除済みリスト。"""
    items = keyterms.split(",") if isinstance(keyterms, str) else list(keyterms)
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        term = raw.strip()
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def existing_surfaces(dict_dir: Path) -> set[str]:
    """dict_dir 配下の全 *.csv (auto_pending.csv 含む) の表層形 (第 1 カラム) の集合。"""
    surfaces: set[str] = set()
    for csv_path in sorted(dict_dir.glob("*.csv")):
        if not csv_path.is_file():
            continue
        for line in csv_path.read_text(encoding="utf-8").splitlines():
            surface = line.split(",", 1)[0].strip()
            if surface:
                surfaces.add(surface)
    return surfaces


def _combined_accent(phrases: list[AccentPhrase]) -> int:
    """複数アクセント句の結合核位置 = 最初の非平板核を結合モーラ位置に換算 (全平板なら 0)。"""
    offset = 0
    for p in phrases:
        if p.accent:
            return offset + p.accent
        offset += p.mora_count
    return 0


def candidate_for(term: str) -> DictCandidate:
    """1 keyterm → 辞書候補 (needs-review)。候補化できない語は ValueError (理由つき)。

    pyopenjtalk が必要 (呼び出し前に `frontend_available()` で確認する)。
    """
    import pyopenjtalk

    surface = normalize(term)  # 実行時パイプライン (L0 → L1) と同じ表層に揃える
    if not surface:
        raise ValueError("L0 正規化で空になる (絵文字・記号のみ等)")
    if _BAD_SURFACE.search(surface):
        raise ValueError(f"表層に CSV/照合を壊す文字 (空白・カンマ等) を含む: {surface!r}")
    if not _JA_CHAR.search(surface):
        raise ValueError(
            f"英字表層 ({surface!r}) は MeCab 辞書でなく L0 の KNOWN_WORDS で読みを固定する"
        )

    reading_parts: list[str] = []
    pron_parts: list[str] = []
    for node in pyopenjtalk.run_frontend(surface):
        if int(node.get("mora_size", 0)) <= 0:
            continue  # 記号ノードは読みを構成しない
        pron = norm_kana(str(node.get("pron") or ""))
        reading_parts.append(str(node.get("read") or "") or pron)
        pron_parts.append(pron)
    if not pron_parts:
        raise ValueError("pyopenjtalk が読みを生成できない")

    # 候補は素の予測を記録したいので use_user_dict=False (適用済みグローバル辞書の影響は
    # 残りうる — モジュール docstring 参照)
    phrases = predict_accent(surface, use_user_dict=False)
    return DictCandidate(
        surface=surface,
        reading="".join(reading_parts),
        pron="".join(pron_parts),
        accent=_combined_accent(phrases),
        mora_count=sum(p.mora_count for p in phrases),
    )


def sync_keyterms(
    keyterms: str | Sequence[str], *, dict_dir: Path | None = None
) -> SyncResult:
    """keyterms の辞書候補を auto_pending.csv へ追記する (公開 API。冪等)。

    keyterms はリストまたはカンマ区切り文字列 (bot.py `_expand_theme` の戻り値と同形式)。
    既存辞書 (project_words.csv + auto_pending.csv) に表層がある語はスキップする。
    pyopenjtalk 未導入なら RuntimeError、辞書ディレクトリ不在なら FileNotFoundError
    (bot.py へ配線する際は呼び出し側で捕捉し、会話本体を止めないこと)。
    """
    reason = frontend_available()
    if reason is not None:
        raise RuntimeError(reason)
    if dict_dir is None:
        dict_dir = find_dict_dir()
    if dict_dir is None or not dict_dir.is_dir():
        raise FileNotFoundError(
            "アクセント辞書ディレクトリ (data/accent_dict) が見つからない "
            f"(dict_dir 引数 / CLI --dict-dir / 環境変数 {ENV_DICT_DIR} で指定可)"
        )
    pending_path = dict_dir / PENDING_DICT_FILENAME

    known = existing_surfaces(dict_dir)
    added: list[DictCandidate] = []
    skipped_existing: list[str] = []
    skipped_invalid: list[tuple[str, str]] = []
    for term in parse_keyterms(keyterms):
        if normalize(term) in known:  # 表層 (L0 正規化後) で重複排除 → 冪等
            skipped_existing.append(term)
            continue
        try:
            cand = candidate_for(term)
        except ValueError as e:
            skipped_invalid.append((term, str(e)))
            continue
        known.add(cand.surface)  # 入力内の表層重複 (正規化後に同一になる語) も 1 回だけ
        added.append(cand)

    if added:
        text = pending_path.read_text(encoding="utf-8") if pending_path.is_file() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(c.csv_row() + "\n" for c in added)
        pending_path.write_text(text, encoding="utf-8")

    return SyncResult(
        added=tuple(added),
        skipped_existing=tuple(skipped_existing),
        skipped_invalid=tuple(skipped_invalid),
        pending_path=pending_path,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="テーマ keyterms をアクセント辞書候補 (auto_pending.csv) へ同期する"
    )
    ap.add_argument("--keyterms", help='カンマ区切りの keyterms (例: "宮崎駿, スタジオジブリ")')
    ap.add_argument(
        "--keyterms-file",
        help="keyterms を書いたテキストファイル (カンマ区切り・改行区切りの混在可)",
    )
    ap.add_argument("--dict-dir", help="辞書ディレクトリ (既定: data/accent_dict を自動探索)")
    args = ap.parse_args(argv)

    terms: list[str] = []
    if args.keyterms:
        terms += parse_keyterms(args.keyterms)
    if args.keyterms_file:
        content = Path(args.keyterms_file).read_text(encoding="utf-8")
        terms += parse_keyterms(content.replace("\n", ","))
    if not terms:
        ap.error("--keyterms か --keyterms-file で keyterm を 1 語以上指定する")

    reason = frontend_available()
    if reason is not None:
        print(f"[dict-sync] {reason}")
        return 1
    result = sync_keyterms(terms, dict_dir=Path(args.dict_dir) if args.dict_dir else None)

    for cand in result.added:
        print(f"[dict-sync] 追加: {cand.csv_row()}")
    for term in result.skipped_existing:
        print(f"[dict-sync] スキップ (登録済): {term}")
    for term, why in result.skipped_invalid:
        print(f"[dict-sync] スキップ ({why}): {term}")
    print(
        f"[dict-sync] {result.pending_path}: 追加 {len(result.added)} 件 / "
        f"スキップ {len(result.skipped_existing) + len(result.skipped_invalid)} 件 "
        "(候補は needs-review。人間レビューで project_words.csv へ昇格するまで発話に反映されない)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
