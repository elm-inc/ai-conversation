"""日本語 TTS フロントエンド L1 — G2P + アクセント推定 (設計 japanese-tts-optimization §5-L1)。

pyopenjtalk-plus (MIT, 商用クリーン) をラップし、テキストから
音素列 / モーラ / アクセント核 / アクセント句境界 / 読み (カタカナ) を取り出す。
全 TTS アダプタ・ハーネス (apps/tts-bench) が共有する中核層で、pyopenjtalk は遅延 import
(未導入環境では `frontend_available()` が導入案内を返し、呼び出し側が skip する)。

3 つの精度レバー (設計 §5-L1):
1. **ユーザー辞書** — `data/accent_dict/*.csv` (OpenJTalk/NAIST-JDIC 形式) を
   `mecab_dict_index()` でコンパイルし `update_global_jtalk_with_user_dict()` で適用。
   固有名詞の読み + アクセント核を 100% 固定する。初回の `predict_accent()` 呼び出しで
   自動適用される (`AICONV_ACCENT_DICT_DIR` でディレクトリ上書き、無ければ辞書なしで続行)。
   手動制御は `load_user_dict()` / `reset_user_dict()`。辞書形式は data/accent_dict/README.md。
2. **marine (DNN アクセント推定, Apache-2.0)** — `run_marine=True` 引数または環境変数
   `AICONV_RUN_MARINE=1` で有効化。未導入なら警告して規則ベースへフォールバック
   (graceful)。導入: `uv sync --inexact --extra frontend-marine`。
3. **手動上書き** — 残る誤りは AccentPhrase 列を呼び出し側で編集する (将来の L2/L3 連携)。

フォールバック方針 (設計 §5-L1): 辞書未登録語は marine → 規則ベースの順。誤読しても落ちない。

アクセント抽出ロジック (P0 ハーネス apps/tts-bench/accent_check.py から移設):
- アクセント句境界は /F:...@f5_ の f5 (呼気段落内のアクセント句位置) の変化 +
  pau/sil による呼気段落の切り替わりで検出する (HTS 日本語ラベル仕様)。
- モーラ数 = f1、アクセント型 = f2 (/F:f1_f2#...)。
- OpenJTalk は平板型を「核位置 = モーラ数」(句末モーラ) で表現する (実測: 飴が → /F:3_3)。
  句内では下降が起きず音響的に平板と等価なので、表記の慣行に合わせて 0 に正規化する。
- 読みは run_frontend の NJD pron を chain_flag で句に結合して充てる。句数が合わない
  場合は読みなし (モーラ数表示) にフォールバックする。
"""

from __future__ import annotations

import importlib.util
import os
import re
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

ENV_DICT_DIR = "AICONV_ACCENT_DICT_DIR"  # ユーザー辞書ディレクトリの上書き
ENV_RUN_MARINE = "AICONV_RUN_MARINE"  # "1"/"true"/"on" で marine を既定有効化

_DICT_RELPATH = Path("data") / "accent_dict"

_LBL_PHONE = re.compile(r"\-(.+?)\+")
_LBL_F = re.compile(r"/F:(\d+)_(\d+)#\d+_\d+@(\d+)_")  # f1=モーラ数, f2=アクセント型, f5=句位置
# 拗音等の小書きは直前のカナと合わせて 1 モーラ ("ッ"/"ー"/"ン" は独立モーラ)
_SMALL_KANA = set("ャュョァィゥェォヮ")


def frontend_available() -> str | None:
    """pyopenjtalk が import 可能か。None = 可、str = 不可の理由 (導入案内)。"""
    if importlib.util.find_spec("pyopenjtalk") is None:
        return (
            "pyopenjtalk 未導入のため日本語フロントエンド (L1) をスキップ: "
            "uv sync --inexact --extra frontend を実行 (pyopenjtalk-plus, MIT)"
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


def norm_kana(s: str) -> str:
    """比較用正規化: 無声化記号・空白を除去。"""
    return s.replace("’", "").replace(" ", "")


def mora_count_kana(reading: str) -> int:
    """カタカナ読みのモーラ数 (小書きは直前のカナと合わせて 1 モーラ)。"""
    return sum(1 for ch in norm_kana(reading) if ch not in _SMALL_KANA)


# ---------------------------------------------------------------------------
# marine (DNN アクセント推定) — graceful フォールバック
# ---------------------------------------------------------------------------

_marine_failed = False  # 一度失敗したらプロセス内では規則ベースに固定 (警告は 1 回)


def _resolve_marine(run_marine: bool | None) -> bool:
    """引数 > 環境変数 の優先で marine 利用を決める (失敗済みなら常に False)。"""
    if run_marine is None:
        run_marine = os.environ.get(ENV_RUN_MARINE, "").strip().lower() in {"1", "true", "on"}
    return run_marine and not _marine_failed


def _fullcontext(text: str, run_marine: bool) -> list[str]:
    """フルコンテキストラベル抽出。marine 失敗時は規則ベースへフォールバックする。"""
    import pyopenjtalk

    global _marine_failed
    if run_marine:
        try:
            return list(pyopenjtalk.extract_fullcontext(text, run_marine=True))
        except Exception as e:  # noqa: BLE001 — marine 未導入/失敗でも落とさない (設計 §5-L1)
            _marine_failed = True
            warnings.warn(
                f"marine が利用できないため規則ベースにフォールバック: {e} "
                "(導入: uv sync --inexact --extra frontend-marine)",
                stacklevel=2,
            )
    return list(pyopenjtalk.extract_fullcontext(text))


# ---------------------------------------------------------------------------
# ユーザー辞書 (data/accent_dict/*.csv)
# ---------------------------------------------------------------------------

_user_dict_applied: bool | None = None  # None=未試行 / True=適用済 / False=辞書なし
_compiled_dir: tempfile.TemporaryDirectory[str] | None = None


def find_dict_dir() -> Path | None:
    """既定のユーザー辞書ディレクトリを探す。

    優先順: 環境変数 AICONV_ACCENT_DICT_DIR → cwd から上方探索 → 本ファイルから上方探索
    (editable install でリポジトリの data/accent_dict を見つける)。
    """
    env = os.environ.get(ENV_DICT_DIR, "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    for base in (Path.cwd(), Path(__file__).resolve()):
        for parent in (base, *base.parents):
            cand = parent / _DICT_RELPATH
            if cand.is_dir():
                return cand
    return None


def compile_user_dict(csv_path: Path, out_dir: Path) -> Path:
    """OpenJTalk ユーザー辞書 CSV を mecab_dict_index でバイナリ辞書へコンパイルする。"""
    import pyopenjtalk

    out = out_dir / (csv_path.stem + ".dic")
    pyopenjtalk.mecab_dict_index(str(csv_path), str(out))
    return out


def load_user_dict(dict_dir: Path | None = None) -> list[Path]:
    """dict_dir (省略時は find_dict_dir()) の *.csv をコンパイルしてグローバル適用する。

    戻り値は適用した CSV のリスト (辞書ディレクトリ/CSV が無ければ空)。
    コンパイル先はプロセス毎の一時ディレクトリ (リポジトリにバイナリを残さない)。
    """
    global _user_dict_applied, _compiled_dir
    import pyopenjtalk

    if dict_dir is None:
        dict_dir = find_dict_dir()
    if dict_dir is None or not dict_dir.is_dir():
        _user_dict_applied = False
        return []
    csvs = sorted(p for p in dict_dir.glob("*.csv") if p.is_file())
    if not csvs:
        _user_dict_applied = False
        return []
    if _compiled_dir is None:
        _compiled_dir = tempfile.TemporaryDirectory(prefix="aiconv_accent_dict_")
    out_dir = Path(_compiled_dir.name)
    dics = [compile_user_dict(c, out_dir) for c in csvs]
    pyopenjtalk.update_global_jtalk_with_user_dict([str(d) for d in dics])
    _user_dict_applied = True
    return csvs


def ensure_user_dict() -> bool:
    """既定のユーザー辞書を一度だけ適用する (graceful)。True = 適用済み。

    辞書が見つからない・コンパイルに失敗しても落とさない (誤読しても破綻させない方針)。
    """
    global _user_dict_applied
    if _user_dict_applied is not None:
        return _user_dict_applied
    if frontend_available() is not None:
        return False  # pyopenjtalk が無い (未試行のままにし、導入後に再試行できる)
    try:
        return bool(load_user_dict())
    except Exception as e:  # noqa: BLE001 — 辞書なしで続行 (フォールバック方針)
        _user_dict_applied = False
        warnings.warn(f"ユーザー辞書の適用に失敗 (辞書なしで続行): {e}", stacklevel=2)
        return False


def reset_user_dict() -> None:
    """グローバル辞書を解除し、次回 ensure_user_dict() で再適用させる (テスト・再読込用)。"""
    global _user_dict_applied
    import pyopenjtalk

    pyopenjtalk.unset_user_dict()
    _user_dict_applied = None


# ---------------------------------------------------------------------------
# G2P / アクセント句予測
# ---------------------------------------------------------------------------


def _labels_to_phrases(labels: list[str]) -> list[tuple[int, int]]:
    """フルコンテキストラベル列 → [(アクセント核, モーラ数)] (句境界検出込み)。"""
    phrases: list[tuple[int, int]] = []
    breath_group = 0
    cur_key: tuple[int, int] | None = None
    for lab in labels:
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
    return phrases


def _phrase_readings(text: str, run_marine: bool) -> list[str]:
    """NJD ノードを chain_flag でアクセント句に結合した読み (カタカナ) のリスト。"""
    import pyopenjtalk

    nodes = (
        pyopenjtalk.run_frontend(text, run_marine=True)
        if run_marine
        else pyopenjtalk.run_frontend(text)
    )
    readings: list[str] = []
    for node in nodes:
        pron = str(node.get("pron") or "")
        if int(node.get("mora_size", 0)) <= 0 or not pron:
            continue  # 記号 (、。等) は句を構成しない
        if int(node.get("chain_flag", 0)) == 1 and readings:
            readings[-1] += pron
        else:
            readings.append(pron)
    return readings


def predict_accent(
    text: str, *, run_marine: bool | None = None, use_user_dict: bool = True
) -> list[AccentPhrase]:
    """テキスト → アクセント句列 (読み + 核位置 + モーラ数)。

    text は正規化済み (`text_normalize.normalize`) を推奨 (生テキストでも動作はする)。
    use_user_dict=True で既定ユーザー辞書を遅延適用する (一度適用するとプロセス内で
    グローバルに有効。完全に外すには reset_user_dict() を呼ぶ)。
    """
    if use_user_dict:
        ensure_user_dict()
    marine = _resolve_marine(run_marine)
    labels = _fullcontext(text, marine)
    phrases = _labels_to_phrases(labels)
    readings = _phrase_readings(text, marine and not _marine_failed)
    if len(readings) != len(phrases):  # NJD と整合しない場合は読みなしにフォールバック
        readings = [""] * len(phrases)
    return [
        AccentPhrase(reading=norm_kana(r), accent=acc, mora_count=n)
        for r, (acc, n) in zip(readings, phrases, strict=True)
    ]


def g2p_phonemes(
    text: str, *, run_marine: bool | None = None, use_user_dict: bool = True
) -> list[str]:
    """テキスト → 音素列 (フルコンテキストラベル由来。文頭/文末の sil は除き pau は残す)。"""
    if use_user_dict:
        ensure_user_dict()
    labels = _fullcontext(text, _resolve_marine(run_marine))
    phones: list[str] = []
    for lab in labels:
        m = _LBL_PHONE.search(lab)
        if m is None or m.group(1) == "sil":
            continue
        phones.append(m.group(1))
    return phones


def format_phrases(phrases: list[AccentPhrase]) -> str:
    """アクセント句列の表示形式 "アメガ[1] フルラシイヨ[1]" (0=平板)。"""
    return " ".join(p.fmt() for p in phrases)


def predicted_reading(phrases: list[AccentPhrase]) -> str:
    """文全体の予測読み (カタカナ連結)。"""
    return "".join(p.reading for p in phrases)
