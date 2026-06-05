"""AI judge (AIC-7 Phase C): 録音した AI 同士会話をルーブリック採点する。

record_conversation.py が残す各話者の `<base>-{a,b}.pcm.log` から、時系列トランスクリプト
(発話+時刻) と遅延指標 (LLM TTFB) を組み立て、LLM に**自然さ / 人格一貫性 /
ターンテイキング / 整合性 / 遅延体感**を 1-5 で採点させ、不自然箇所を引用付きで返す。
デプロイ前後の「会話品質回帰」を人手レスで検出する用途 (設計: docs/design conversation-tester C)。

    uv run python judge.py                      # 直近の録音を採点
    uv run python judge.py /tmp/ai-conv-ja-1780623556
    uv run python judge.py --model claude-opus-4-8

異種ベンダーで groupthink を避けたい場合は --model に別系統を指定 (ADR-0001 の精神)。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
import urllib.request
from datetime import datetime

from director import _tok

try:
    from presets import PRESETS  # 話者名の自動解決に使う (無くても動く)
except Exception:  # noqa: BLE001
    PRESETS = {}

_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")
_TTS = re.compile(r"Generating TTS \[(.+?)\]")
_TTFB = re.compile(r"AnthropicLLMService#0 TTFB: ([0-9.]+)s")
_STOP = "_on_user_turn_stopped"


def _latest_base() -> str | None:
    # ai-conv-(record_conversation/director) と ai-text-(record_text) の両方を対象にする
    logs = glob.glob("/tmp/ai-conv-*-a.pcm.log") + glob.glob("/tmp/ai-text-*-a.pcm.log")
    logs = sorted(logs, key=os.path.getmtime, reverse=True)
    return logs[0][: -len("-a.pcm.log")] if logs else None


def _speaker_names(base: str) -> tuple[str, str]:
    """base 名 (ai-{conv,text}-<preset>-<ts>) から preset の話者名を解決。無ければ あい/ゆう。"""
    m = re.search(r"ai-(?:conv|text)-([a-z]+)-\d+$", base)
    if m and m.group(1) in PRESETS:
        sp = PRESETS[m.group(1)]["speakers"]
        return sp[0]["name"], sp[1]["name"]
    return "あい", "ゆう"


def _parse_log(path: str) -> tuple[list[tuple[datetime, str]], list[float], list[datetime]]:
    """1 話者ログから (発話[(時刻,text)], LLM TTFB 値, turn_stopped 時刻) を抽出。"""
    utts: list[tuple[datetime, str]] = []
    ttfb: list[float] = []
    stops: list[datetime] = []
    if not os.path.isfile(path):
        return utts, ttfb, stops
    for ln in open(path, encoding="utf-8", errors="ignore"):
        tm = _TS.match(ln)
        t = datetime.strptime(tm.group(1), "%Y-%m-%d %H:%M:%S.%f") if tm else None
        if (mu := _TTS.search(ln)) and t:
            utts.append((t, mu.group(1)))
        elif mt := _TTFB.search(ln):
            ttfb.append(float(mt.group(1)))
        elif _STOP in ln and t:
            stops.append(t)
    return utts, ttfb, stops


def build_transcript(base: str) -> tuple[str, dict]:
    """時系列トランスクリプト文字列と遅延指標を返す。"""
    name_a, name_b = _speaker_names(base)
    ua, ta, _ = _parse_log(f"{base}-a.pcm.log")
    ub, tb, _ = _parse_log(f"{base}-b.pcm.log")
    rows = sorted([(t, name_a, txt) for t, txt in ua] + [(t, name_b, txt) for t, txt in ub])
    if not rows:
        return "", {}
    # 1 ターンが複数文 = 複数 TTS 行になるため、連続する同一話者の発話を 1 ターンに統合する
    # (統合しないと judge が「0.1秒差の連続発話=ターンテイキング破綻」と誤検出する)。
    turns: list[tuple[datetime, str, str]] = []
    for t, who, txt in rows:
        if turns and turns[-1][1] == who:
            turns[-1] = (turns[-1][0], who, turns[-1][2] + txt)
        else:
            turns.append((t, who, txt))
    t0 = turns[0][0]
    lines = []
    for t, who, txt in turns:
        sec = (t - t0).total_seconds()
        lines.append(f"[{int(sec // 60):02d}:{sec % 60:04.1f}] {who}: {txt}")
    ttfb = ta + tb
    metrics = {
        "turns": len(turns),
        "llm_ttfb_median": round(statistics.median(ttfb), 2) if ttfb else None,
        "llm_ttfb_max": round(max(ttfb), 2) if ttfb else None,
    }
    return "\n".join(lines), metrics


RUBRIC = (
    "あなたは音声対話の品質を評価する厳格な審査員です。以下は AI 2 体による会話の時系列"
    "トランスクリプト (各行 [mm:ss] 話者: 発話) と遅延指標です。次の5軸を各 1-5 点 (5=最良) で"
    "採点し、各軸について具体的な問題箇所を発話の**引用付き**で挙げてください。甘くせず、"
    "不自然さを積極的に指摘してください。\n"
    "- 自然さ: 不自然な言い回し・機械的な繰り返し・冗長さがないか\n"
    "- 人格一貫性: 各話者の口調/設定が一貫し OOC (キャラ崩れ) がないか\n"
    "- ターンテイキング: かぶり/不自然な沈黙/相手の発話を無視していないか\n"
    "- 整合性: 話が噛み合っているか (同じ質問の繰り返し・自己紹介ループ・話題が飛ぶ等の劣化)\n"
    "- 遅延体感: 応答までの間 (遅延指標を参考に。TTFB が大きいほど減点)\n\n"
    "出力は次の JSON のみ (前後に説明文を付けない):\n"
    '{"scores":{"自然さ":n,"人格一貫性":n,"ターンテイキング":n,"整合性":n,"遅延体感":n},'
    '"issues":[{"axis":"..","quote":"..","comment":".."}],'
    '"summary":"総評1-2文","regression_risk":"low|medium|high"}'
)


def judge_with_claude(transcript: str, metrics: dict, model: str) -> dict:
    key = _tok("anthropic") or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY (~/.anthropic_token) が無い")
    prompt = (
        f"{RUBRIC}\n\n# 遅延指標\n{json.dumps(metrics, ensure_ascii=False)}"
        f"\n\n# トランスクリプト\n{transcript}"
    )
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.load(r)
    text = "".join(b.get("text", "") for b in resp.get("content", []))
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def _print_report(base: str, metrics: dict, result: dict) -> None:
    print(f"\n=== AI judge: {os.path.basename(base)} ===")
    print(f"ターン数: {metrics.get('turns')}  LLM TTFB median/max: "
          f"{metrics.get('llm_ttfb_median')}/{metrics.get('llm_ttfb_max')}s")
    scores = result.get("scores", {})
    total = sum(scores.values())
    print(f"\n■ スコア (計 {total}/25):")
    for axis, sc in scores.items():
        print(f"  {axis:8s}: {'★' * int(sc)}{'☆' * (5 - int(sc))} ({sc})")
    print(f"\n■ 回帰リスク: {result.get('regression_risk')}")
    print(f"■ 総評: {result.get('summary')}")
    issues = result.get("issues", [])
    if issues:
        print("\n■ 指摘:")
        for it in issues:
            print(f"  [{it.get('axis')}] 「{it.get('quote')}」")
            print(f"      → {it.get('comment')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", help="録音の base パス (省略時は直近)")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="審査 LLM")
    ap.add_argument("--json", action="store_true", help="JSON のみ出力 (CI 用)")
    args = ap.parse_args()

    base = args.base or _latest_base()
    if not base:
        raise SystemExit("録音ログが見つかりません (/tmp/ai-conv-*-a.pcm.log)")
    base = base[: -len("-a.pcm.log")] if base.endswith("-a.pcm.log") else base

    transcript, metrics = build_transcript(base)
    if not transcript:
        raise SystemExit(f"トランスクリプトが空です: {base}")
    result = judge_with_claude(transcript, metrics, args.model)

    if args.json:
        print(json.dumps({"base": base, "metrics": metrics, "result": result}, ensure_ascii=False))
    else:
        _print_report(base, metrics, result)
        if result.get("regression_risk") == "high":
            sys.exit(1)  # CI で回帰検出に使えるよう high は非ゼロ終了


if __name__ == "__main__":
    main()
