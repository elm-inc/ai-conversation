"""テキストレベル AI 会話録音 (AIC-7 検証 B)。

dual-local の音声会話は相手の TTS 音声を WebRTC 経由で STT する過程で誤認識が混入し、整合性が
崩れる(「町並み→調味料」「アオアシ→青足」)。本スクリプトは **会話ロジックをテキストで直接回し**
(STT を介さない)、各発話を **REST TTS でフル品質生成**(ストリーミング継ぎ目なし)して時刻整列で
stereo 録音する。STT 誤認識・ストリーミング継ぎ目の両方を原理的に排除したコンテンツ向け録音。

    uv run python record_text.py --preset ja --theme "おすすめの映画" --turns 10

judge は record_conversation と同じログ形式 (<base>-{a,b}.pcm.log) を吐くのでそのまま採点可能。
注: 実音声パイプライン(STT 含む)の回帰検証は従来の record_conversation を使う。

--tts espnet / voicevox でセルフホスト新アダプタ (src/aiconv/adapters/tts_{espnet,voicevox}.py,
ports.TTSProvider 経由, L0 正規化込み) でも録音できる (AIC-9 P2)。新アダプタの出力は core 規約の
16kHz PCM のため、トラック組み立て時に SR (24kHz) へアップサンプルする (帯域は 16kHz 相当)。
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
# 発話ごとに独立生成するため、stability を上げてトーンのブレ/歪みを抑える (audio bot と同等)。
VOICE_SETTINGS = {"stability": 0.55, "similarity_boost": 0.8, "use_speaker_boost": True}
# 完全 L/R パンは片耳だけで不自然なので、両耳に出しつつ軽く左右に配置する gentle pan。
PAN_NEAR, PAN_FAR = 0.85, 0.5


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
    body = json.dumps(
        {"text": text, "model_id": model, "voice_settings": VOICE_SETTINGS}
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"xi-api-key": key, "content-type": "application/json"},
        method="POST",
    )
    return _post(req, 90)


def _make_local_adapters(kind: str, vv_speakers: str) -> list:
    """--tts aivis/espnet/voicevox 用の TTSProvider アダプタを話者 A/B 分つくる。"""
    if kind == "espnet":
        from aiconv.adapters.tts_espnet import EspnetTTS

        adapter = EspnetTTS()
        return [adapter, adapter]  # JSUT 単一話者モデル (話者分離は ESPnet 話者適応後の課題)
    if kind in ("aivis", "voicevox"):
        from aiconv.adapters.tts_aivis import AivisSpeechTTS

        ids = [int(x) for x in vv_speakers.split(",") if x.strip()]
        if len(ids) != 2:
            raise SystemExit("--vv-speakers は A,B の 2 つの話者 id")
        return [AivisSpeechTTS(speaker=ids[0]), AivisSpeechTTS(speaker=ids[1])]
    raise SystemExit(f"未知の --tts: {kind}")


def _local_tts_pcm(adapter, text: str) -> bytes:
    """新アダプタ (ports.TTSProvider) で一括合成し、SR の mono PCM へ整える。

    アダプタ出力は core 規約の 16kHz mono PCM (合成前の L0 正規化もアダプタ側で実施)。
    """
    import asyncio

    from aiconv.adapters._engines.resample import resample_pcm16

    async def _collect() -> bytes:
        async def chunks():
            yield text

        buf = bytearray()
        async for frame in adapter.synthesize(chunks()):
            buf += frame.data
        return bytes(buf)

    return resample_pcm16(asyncio.run(_collect()), 16_000, SR)


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
    ap.add_argument("--model", help="LLM 上書き (例 claude-sonnet-4-6)。既定は preset")
    ap.add_argument(
        "--tts", default="elevenlabs", choices=("elevenlabs", "aivis", "espnet", "voicevox"),
        help="TTS エンジン。aivis=本命 (AivisSpeech/SBV2, VOICEVOX互換)。espnet/voicevox は評価用",
    )
    ap.add_argument(
        "--vv-speakers", default="888753760,1878365376",
        help="話者 id ペア A,B (aivis/voicevox 用。既定 まお,コハク)。/speakers で確認",
    )
    args = ap.parse_args()

    needed = ["anthropic"] + (["elevenlabs"] if args.tts == "elevenlabs" else [])
    for t in needed:
        if not _tok(t):
            raise SystemExit(f"~/.{t}_token が無い")
    akey, ekey = _tok("anthropic"), _tok("elevenlabs")

    preset = json.loads(json.dumps(PRESETS[args.preset]))
    spk = preset["speakers"]
    lang, tts_model = preset["language"], preset["tts_model"]
    model = args.model or preset["anthropic_model"]

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
    print(f"[tts] {args.tts} でフル品質生成中...")
    adapters = None if args.tts == "elevenlabs" else _make_local_adapters(
        args.tts, args.vv_speakers
    )
    segments = []  # (who_idx, pcm)
    for turn, (_who, text) in enumerate(history):
        idx = turn % 2
        if adapters is None:
            pcm = _eleven_pcm(text, spk[idx]["voice"], tts_model, ekey)
        else:
            pcm = _local_tts_pcm(adapters[idx], text)
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
    # gentle pan: A は左寄り / B は右寄り、ただし両者とも両耳に出す (自然な聞き心地)
    stereo = audioop.add(
        audioop.tostereo(la, 2, PAN_NEAR, PAN_FAR),
        audioop.tostereo(lb, 2, PAN_FAR, PAN_NEAR), 2,
    )
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
