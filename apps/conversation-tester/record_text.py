"""テキストレベル AI 会話録音 (AIC-7 検証 B)。

dual-local の音声会話は相手の TTS 音声を WebRTC 経由で STT する過程で誤認識が混入し、整合性が
崩れる(「町並み→調味料」「アオアシ→青足」)。本スクリプトは **会話ロジックをテキストで直接回し**
(STT を介さない)、各発話を **REST TTS でフル品質生成**(ストリーミング継ぎ目なし)して時刻整列で
stereo 録音する。STT 誤認識・ストリーミング継ぎ目の両方を原理的に排除したコンテンツ向け録音。

    uv run python record_text.py --preset ja --theme "おすすめの映画" --turns 10

judge は record_conversation と同じログ形式 (<base>-{a,b}.pcm.log) を吐くのでそのまま採点可能。
注: 実音声パイプライン(STT 含む)の回帰検証は従来の record_conversation を使う。
"""

from __future__ import annotations

import argparse
import audioop
import json
import time
import urllib.request
import wave
from datetime import datetime, timedelta
from pathlib import Path

from director import _tok
from presets import AI_PERSONA, PRESETS, SR, THEME_TEMPLATES
from record_conversation import expand_theme

GAP_S = 0.4  # ターン間の無音 (自然な間)


def _post(req: urllib.request.Request, timeout: float, attempts: int = 3) -> bytes:
    """transient な timeout/接続エラーをリトライ (指数バックオフ)。"""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            if i == attempts - 1:
                raise
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("unreachable")


def _anthropic(system: str, user: str, model: str, key: str, max_tokens: int = 220) -> str:
    body = json.dumps(
        {"model": model, "max_tokens": max_tokens, "system": system,
         "messages": [{"role": "user", "content": user}]}
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST",
    )
    resp = json.loads(_post(req, 60))
    return "".join(b.get("text", "") for b in resp.get("content", [])).strip()


def _eleven_pcm(text: str, voice: str, model: str, key: str) -> bytes:
    """REST TTS でフル品質の raw PCM (24kHz mono 16-bit) を一括生成 (ストリーミング継ぎ目なし)。"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_24000"
    body = json.dumps({"text": text, "model_id": model}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"xi-api-key": key, "content-type": "application/json"},
        method="POST",
    )
    return _post(req, 90)


def _system_for(sp: dict, theme: str, lang: str, brief: str) -> str:
    persona = sp["persona"] or AI_PERSONA
    parts = [persona]
    if theme:
        tpl = THEME_TEMPLATES.get(lang, THEME_TEMPLATES["en"])
        parts.append(tpl["opener" if sp["kickoff"] else "responder"].format(theme=theme))
    elif sp["scenario"]:
        parts.append(sp["scenario"])
    if brief:
        parts.append(f"# 参考知識 (事実を取り違えない)\n{brief}")
    return "\n\n".join(parts)


def _write_log(base: str, who_idx: int, t: datetime, text: str) -> None:
    suffix = "a" if who_idx == 0 else "b"
    line = (f"{t.strftime('%Y-%m-%d %H:%M:%S.%f')} | DEBUG | "
            f"services.elevenlabs.tts:run_tts Service#0: Generating TTS [{text}]\n")
    with open(f"{base}-{suffix}.pcm.log", "a", encoding="utf-8") as f:
        f.write(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="ja", choices=sorted(PRESETS))
    ap.add_argument("--theme")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--out-dir", default="/tmp")
    ap.add_argument("--no-enrich", action="store_true")
    args = ap.parse_args()

    for t in ("anthropic", "elevenlabs"):
        if not _tok(t):
            raise SystemExit(f"~/.{t}_token が無い")
    akey, ekey = _tok("anthropic"), _tok("elevenlabs")

    preset = json.loads(json.dumps(PRESETS[args.preset]))
    spk = preset["speakers"]
    lang, model, tts_model = preset["language"], preset["anthropic_model"], preset["tts_model"]

    brief = ""
    if args.theme and not args.no_enrich:
        _, brief = expand_theme(args.theme, lang)
        print(f"[enrich] knowledge={len(brief)}字")

    stamp = int(time.time())
    base = str(Path(args.out_dir) / f"ai-text-{args.preset}-{stamp}")
    for s in ("a", "b"):
        open(f"{base}-{s}.pcm.log", "w").close()

    # --- 会話ロジック (テキスト、STT なし) ---
    print(f"[text-conv] {spk[0]['name']} ⇄ {spk[1]['name']} theme={args.theme} turns={args.turns}")
    history: list[tuple[str, str]] = []
    for turn in range(args.turns):
        sp = spk[turn % 2]
        sys_full = _system_for(sp, args.theme or "", lang, brief)
        if history:
            transcript = "\n".join(f"{w}: {t}" for w, t in history)
            if lang == "ja":
                user = (f"これまでの会話:\n{transcript}\n\nあなたは「{sp['name']}」です。"
                        "次のあなたの発話だけを1〜2文で返してください(名前ラベルは付けない)。")
            else:
                user = (f"Conversation so far:\n{transcript}\n\nYou are {sp['name']}. "
                        "Reply with only your next line, one or two sentences (no name label).")
        elif args.theme:
            tpl = THEME_TEMPLATES.get(lang, THEME_TEMPLATES["en"])
            user = tpl["kickoff"].format(theme=args.theme)
        else:
            user = "自然に挨拶して会話を始めてください。" if lang == "ja" else "Greet and start."
        line = _anthropic(sys_full, user, model, akey)
        history.append((sp["name"], line))
        print(f"  {sp['name']}: {line}")

    # --- TTS フル品質生成 + 時刻整列で stereo 組み立て ---
    print("[tts] REST でフル品質生成中...")
    segments = []  # (who_idx, pcm)
    for turn, (_who, text) in enumerate(history):
        idx = turn % 2
        pcm = _eleven_pcm(text, spk[idx]["voice"], tts_model, ekey)
        segments.append((idx, pcm))

    base_t = datetime.now()
    chans = [bytearray(), bytearray()]
    cursor = 0.0  # 秒
    for (idx, pcm), (_who, text) in zip(segments, history, strict=True):
        # 全チャンネルを cursor までゼロ埋め
        for c in range(2):
            need = int(cursor * SR) * 2 - len(chans[c])
            if need > 0:
                chans[c] += b"\x00\x00" * (need // 2)
        chans[idx] += pcm
        _write_log(base, idx, base_t + timedelta(seconds=cursor), text)
        cursor += len(pcm) / 2 / SR + GAP_S
    n = max(len(chans[0]), len(chans[1]))
    for c in range(2):
        chans[c] += b"\x00\x00" * ((n - len(chans[c])) // 2)
    nmin = min(len(chans[0]), len(chans[1]))

    def _norm(mono: bytes, target: float = 0.7) -> bytes:  # 声ごとの音量差を揃える
        peak = audioop.max(mono, 2) or 1
        return audioop.mul(mono, 2, (target * 32767) / peak)

    la, lb = _norm(bytes(chans[0][:nmin])), _norm(bytes(chans[1][:nmin]))
    stereo = audioop.add(audioop.tostereo(la, 2, 1, 0), audioop.tostereo(lb, 2, 0, 1), 2)
    out = f"{base}.wav"
    with wave.open(out, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(stereo)
    mb = Path(out).stat().st_size / 1_000_000
    print(f"[recording] 保存: {out} ({mb:.1f} MB, L={spk[0]['name']}/R={spk[1]['name']})")
    print(f"  base={base}")


if __name__ == "__main__":
    main()
