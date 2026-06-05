"""AI同士会話の高品質録音 (AIC-7, 本格版・多言語/多キャラ対応)。

両スピーカーを **ローカルで起動**し、各ボットに **自分の出力 (フル品質) だけ** を録らせ
(RECORD_TRACK=bot)、**共通 T0 で時刻整列**して 1 本の stereo WAV にマージする。WebRTC 越しの
劣化が原理的に出ない。言語/キャラクターは presets.PRESETS で切替 (今後増やせる)。

    uv run python record_conversation.py --preset ja
    uv run python record_conversation.py --preset en --seconds 120
    uv run python record_conversation.py --preset en --voice-a <id> --voice-b <id>

前提: ~/.{deepgram,anthropic,elevenlabs,daily}_token。bot.py を role パラメータで 2 体起動する。
注: ElevenLabs Library voice は「My Voices に追加」済みでないと websocket TTS で無音になる。
"""

from __future__ import annotations

import argparse
import audioop
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

from director import _tok
from presets import PRESETS, SR, THEME_TEMPLATES  # 会話プリセット (キャラ/言語) の単一ソース

VOICE_AGENT = Path(__file__).resolve().parents[1] / "voice-agent"


def create_room(api_key: str, exp_min: int = 30) -> str:
    """Daily REST で一時ルームを作り URL を返す (両ボットが同室 join する)。"""
    body = json.dumps(
        {"properties": {"exp": int(time.time()) + exp_min * 60, "eject_at_room_exp": True}}
    ).encode()
    req = urllib.request.Request(
        "https://api.daily.co/v1/rooms",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["url"]


def expand_theme(theme: str, lang: str) -> tuple[list[str], str]:
    """テーマから STT 辞書語(keyterms)と事実グラウンディング brief を 1 回の LLM 呼び出しで生成。

    任意テーマに自動適応する「テーマで使い分け」の実体。keyterms は STT の用語ブースト、
    knowledge は両ボット system に注入して事実幻覚を抑える (Phase 1)。失敗時は空で続行。
    """
    key = _tok("anthropic")
    if not key:
        return [], ""
    lng = "日本語" if lang == "ja" else "English"
    prompt = (
        f"会話テーマ「{theme}」について {lng} で次を生成し JSON のみ返す:\n"
        f"1. keyterms: 会話に出そうな固有名詞・作品名・人名・専門語を 8〜15 語の配列。\n"
        f"2. knowledge: 事実誤認を防ぐための要点を 3〜5 行で簡潔に ({lng})。\n"
        f'{{"keyterms":["..."],"knowledge":"..."}}'
    )
    body = json.dumps(
        {"model": "claude-sonnet-4-6", "max_tokens": 700,
         "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        text = "".join(b.get("text", "") for b in resp.get("content", []))
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        d = json.loads(text)
        return [str(k) for k in d.get("keyterms", [])], str(d.get("knowledge", ""))
    except Exception as e:  # noqa: BLE001
        print(f"[enrich] theme 展開失敗 ({e}); グラウンディングなしで続行", file=sys.stderr)
        return [], ""


def speaker_env(preset: dict, sp: dict, room: str, rec_path: str, t0: float) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DEEPGRAM_API_KEY": _tok("deepgram"),
            "ANTHROPIC_API_KEY": _tok("anthropic"),
            "ELEVENLABS_API_KEY": _tok("elevenlabs"),
            "DAILY_API_KEY": _tok("daily"),
            "DAILY_ROOM_URL": room,
            "AGENT_NAME": sp["name"],
            "ELEVENLABS_VOICE_ID": sp["voice"],
            "STT_LANGUAGE": preset["language"],
            "STT_MODEL": os.getenv("REC_STT_MODEL", preset["stt_model"]),  # A/B 用に上書き可
            "TTS_MODEL": os.getenv("REC_TTS_MODEL", preset["tts_model"]),  # A/B 用に上書き可
            "ANTHROPIC_MODEL": os.getenv("REC_ANTHROPIC_MODEL", preset["anthropic_model"]),  # A/B可
            "RECORD": "1",
            "RECORD_TRACK": "bot",  # 自分の出力だけ録る (フル品質)
            "RECORD_PATH": rec_path,
            "REC_T0": str(t0),
            # ターンテイキングは既定(snappy)。bot-to-bot では調整しても割り込み不変で
            # レイテンシだけ伸びたため off。REC_VAD_STOP_SECS / REC_TURN_MIN_WORDS で再有効化可。
            "VAD_STOP_SECS": os.getenv("REC_VAD_STOP_SECS", "0.2"),
            "TURN_MIN_WORDS": os.getenv("REC_TURN_MIN_WORDS", "0"),
            "KICKOFF": "0",
            "KICKOFF_ON_JOIN": "1" if sp["kickoff"] else "0",
            "KICKOFF_PROMPT": sp["kickoff_prompt"] or "Start the conversation.",
        }
    )
    if sp["persona"]:
        env["PERSONA_PROMPT"] = sp["persona"]
    if sp["scenario"]:
        env["SCENARIO"] = sp["scenario"]
    if os.getenv("REC_RAW"):  # 診断: 素の TTS も並行保存 (ブツブツ切り分け用)
        env["RECORD_RAW"] = rec_path + ".rawtts"
    return env


def _spawn(env: dict[str, str], log_path: str) -> subprocess.Popen:
    log = open(log_path, "w")  # noqa: SIM115 (プロセス寿命と同じ)
    return subprocess.Popen(
        ["uv", "run", "run_interlocutor.py"], cwd=str(VOICE_AGENT), env=env, stdout=log, stderr=log
    )


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * max(0, int(seconds * SR))  # mono 16-bit


def _load_track(pcm_path: str) -> tuple[float, bytes]:
    """mono 16-bit PCM と .meta の先頭無音 (= 最初の発話 - REC_T0, 秒) を返す。"""
    p = Path(pcm_path)
    audio = p.read_bytes() if p.is_file() else b""
    lead = 0.0
    meta = Path(pcm_path + ".meta")
    if meta.is_file():
        try:
            lead = float(meta.read_text().strip())
        except ValueError:
            lead = 0.0
    return lead, audio


def _norm(mono: bytes, target: float = 0.7) -> bytes:
    if not mono:
        return mono
    peak = audioop.max(mono, 2) or 1
    return audioop.mul(mono, 2, (target * 32767) / peak)


def _trim_lead(a: bytes, b: bytes, head: float, thresh: int = 120) -> tuple[bytes, bytes]:
    """両 mono ch の最初の実音声 onset まで (head 残し) を同量カット。同期は保持。"""
    win = (SR // 50) * 2  # 20ms (bytes)
    n = min(len(a), len(b))
    onset = 0
    for i in range(0, n - win, win):
        if audioop.rms(a[i : i + win], 2) > thresh or audioop.rms(b[i : i + win], 2) > thresh:
            onset = i
            break
    cut = max(0, onset - int(head * SR) * 2)
    cut -= cut % 2  # sample 境界 (16-bit)
    return a[cut:], b[cut:]


def merge_to_wav(a_pcm: str, b_pcm: str, out_wav: str, head: float = 0.3) -> str | None:
    """speaker A=左 / B=右 の stereo にマージ (両方フル品質・時刻整列済み)。

    起動待ち (Daily join + モデル load + 口火) の共通先頭無音は除去し、相対オフセットは保持。
    """
    lead_a, audio_a = _load_track(a_pcm)
    lead_b, audio_b = _load_track(b_pcm)
    if not audio_a and not audio_b:
        return None
    # 相対オフセットは meta lead で保持 (どちらが先に話したか)。
    a = _norm(_silence(lead_a) + audio_a)
    b = _norm(_silence(lead_b) + audio_b)
    n = max(len(a), len(b))
    a = a + b"\x00\x00" * ((n - len(a)) // 2)
    b = b + b"\x00\x00" * ((n - len(b)) // 2)
    n = min(len(a), len(b))  # 端数を揃える
    a, b = a[:n], b[:n]
    # 起動待ち+バッファ遅延等すべての先頭無音を「実音声 onset」基準で除去 (head 残す)。
    # 両ch を同量カットするので相対同期は保持される。
    a, b = _trim_lead(a, b, head)
    stereo = audioop.add(audioop.tostereo(a, 2, 1, 0), audioop.tostereo(b, 2, 0, 1), 2)
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(stereo)
    return out_wav


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="ja", choices=sorted(PRESETS), help="言語/キャラの組")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--out-dir", default="/tmp")
    ap.add_argument("--voice-a", help="speaker A (口火) の voice_id 上書き")
    ap.add_argument("--voice-b", help="speaker B (応答) の voice_id 上書き")
    ap.add_argument("--theme", help="会話の話題 (指定すると両者がこの話題を中心に話す)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="テーマ展開 (STT辞書+グラウンディング) を無効化 (A/B 用)")
    args = ap.parse_args()

    for t in ("deepgram", "anthropic", "elevenlabs", "daily"):
        if not _tok(t):
            raise SystemExit(f"~/.{t}_token が無い")

    preset = json.loads(json.dumps(PRESETS[args.preset]))  # deep copy (上書き用)
    spk = preset["speakers"]
    if args.voice_a:
        spk[0]["voice"] = args.voice_a
    if args.voice_b:
        spk[1]["voice"] = args.voice_b
    if args.theme:  # 話題を注入 (preset 言語のテンプレ)。opener=切り出す役 / responder=乗る役
        tpl = THEME_TEMPLATES.get(preset["language"], THEME_TEMPLATES["en"])
        spk[0]["scenario"] = tpl["opener"].format(theme=args.theme)
        spk[1]["scenario"] = tpl["responder"].format(theme=args.theme)
        spk[0]["kickoff_prompt"] = tpl["kickoff"].format(theme=args.theme)

    room = create_room(_tok("daily"))
    print(f"[room] {room}\n  (聴衆として聴くにはこの URL をブラウザで開く)")
    names = " / ".join(f"{s['name']}({s['voice'][:6]}…)" for s in spk)
    print(f"[preset] {args.preset}: {names}  lang={preset['language']}")
    if args.theme:
        print(f"[theme] {args.theme}")

    stamp = int(time.time())
    base = Path(args.out_dir) / f"ai-conv-{args.preset}-{stamp}"
    a_pcm, b_pcm = f"{base}-a.pcm", f"{base}-b.pcm"
    t0 = time.time()  # 両ボット共通の録音開始基準

    env_a = speaker_env(preset, spk[0], room, a_pcm, t0)
    env_b = speaker_env(preset, spk[1], room, b_pcm, t0)
    if args.theme and not args.no_enrich:  # テーマ展開: STT 辞書 + グラウンディング brief を注入
        keyterms, knowledge = expand_theme(args.theme, preset["language"])
        for e in (env_a, env_b):
            e["STT_KEYTERMS"] = ",".join(keyterms)
            e["KNOWLEDGE_BRIEF"] = knowledge
        print(f"[enrich] keyterms={len(keyterms)}語 / knowledge={len(knowledge)}字"
              + (f"  例: {keyterms[:5]}" if keyterms else ""))

    print(f"[launch] {spk[1]['name']}(応答) → {spk[0]['name']}(口火) 起動 (~{args.seconds}s)...")
    proc_b = _spawn(env_b, f"{b_pcm}.log")  # 応答役を先に入室
    time.sleep(3)
    proc_a = _spawn(env_a, f"{a_pcm}.log")  # 口火役が後から入って話し始める

    procs = [proc_a, proc_b]
    try:
        time.sleep(args.seconds)
    finally:
        for p in procs:
            p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
    print("[done] 両ボット停止")

    out_wav = f"{base}.wav"
    res = merge_to_wav(a_pcm, b_pcm, out_wav)
    if res:
        mb = Path(res).stat().st_size / 1_000_000
        print(f"[recording] 保存: {res} ({mb:.1f} MB, L={spk[0]['name']} / R={spk[1]['name']})")
        for f in (a_pcm, b_pcm, a_pcm + ".meta", b_pcm + ".meta"):
            Path(f).unlink(missing_ok=True)
    else:
        print("[recording] 録音データが空でした", file=sys.stderr)


if __name__ == "__main__":
    main()
