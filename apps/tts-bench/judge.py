"""読み/アクセント予測の LLM サニティチェック + 自然さ採点の人手フック。

apps/conversation-tester/judge.py のルーブリック採点と同じ呼び出し方式
(Anthropic REST + ~/.anthropic_token) を踏襲した薄い別実装
(conversation-tester は録音ログ前提で import 副作用があるため依存しない)。

音声そのものは Anthropic API では採点できないため、役割を分担する:
  1) ここ (--judge): pyopenjtalk 予測読み・アクセントのテキスト整合を LLM で検査
     (固有名詞の読み崩れ・不自然なアクセントの検出)
  2) 自然さ / アクセント自然さ / 明瞭さの音声採点: report.md の聴取シートで人手記入
     (将来 STT roundtrip や音声入力対応 LLM に置換できるようフックとして分離)
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from engines.base import read_token

DEFAULT_MODEL = "claude-sonnet-4-6"

_RUBRIC = (
    "あなたは日本語の読みとアクセントの校正者です。以下は TTS フロントエンド "
    "(pyopenjtalk) が予測した各文の読み (カタカナ) とアクセント句列 "
    "(表記: 読み[核位置]、核位置は何モーラ目で下がるか、0=平板) です。"
    "各文について、読み間違い (固有名詞・数字・英単語の読み崩れ) と"
    "標準語として不自然なアクセントを厳しく検査してください。\n"
    "出力は次の JSON のみ (前後に説明文を付けない):\n"
    '{"items":[{"id":"..","reading_ok":true,"accent_ok":true,'
    '"comment":"問題があれば具体的に"}],"summary":"総評1-2文"}'
)


def judge_readings(
    items: list[dict[str, str]], *, model: str = DEFAULT_MODEL
) -> dict[str, Any]:
    """予測読み/アクセントを LLM で検査する。

    items: [{"id": .., "text": .., "reading": .., "accent": ..}, ...]
    戻り値: {"items": [{"id", "reading_ok", "accent_ok", "comment"}], "summary": ..}
    """
    key = os.environ.get("ANTHROPIC_API_KEY") or read_token("anthropic")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY (~/.anthropic_token) が無い")
    lines = [
        f"- id={it['id']} 原文「{it['text']}」 読み={it['reading'] or '(取得失敗)'} "
        f"アクセント={it['accent']}"
        for it in items
    ]
    prompt = _RUBRIC + "\n\n# 検査対象\n" + "\n".join(lines)
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
    result: dict[str, Any] = json.loads(text)
    return result
