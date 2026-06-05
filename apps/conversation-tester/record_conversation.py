"""AI同士会話の高品質録音 (AIC-7, 本格版・多言語/多キャラ対応)。

両スピーカーを **ローカルで起動**し、各ボットに **自分の出力 (フル品質) だけ** を録らせ
(RECORD_TRACK=bot)、**共通 T0 で時刻整列**して 1 本の stereo WAV にマージする。WebRTC 越しの
劣化が原理的に出ない。言語/キャラクターは PRESETS で切替 (今後増やせる)。

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
import signal
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

from director import YUU_PERSONA, YUU_SCENARIO, YUU_VOICE_ID, _tok

VOICE_AGENT = Path(__file__).resolve().parents[1] / "voice-agent"
SR = 24000  # bot track のサンプルレート (bot.py RECORD_SAMPLE_RATE と一致)
AI_VOICE_ID = "lhTvHflPVOqgSWyuWQry"  # あい本番の声

# --- 英語ペルソナ (en preset) ---
ALEX_PERSONA = (
    "You are Alex, a curious and easygoing English speaker having a casual chat with Sam. "
    "Speak in short, natural spoken English, one or two sentences at a time. React to what "
    "the other person says and share a little about yourself. Greet only once at the very "
    "start; do not repeat greetings later. Your words are read aloud, so no emojis, symbols, "
    "bullet points, or URLs."
)
SAM_PERSONA = (
    "You are Sam, a warm and talkative English speaker chatting with Alex. Speak in short, "
    "natural spoken English, one or two sentences at a time, reacting to Alex and adding your "
    "own thoughts. Greet only once at the start, then keep the conversation flowing from the "
    "topic. No emojis or symbols, since this is read aloud."
)
SAM_SCENARIO = (
    "Goal: 1) a light greeting and how things are going, 2) bring up weekend plans, 3) drift "
    "naturally into the weather, 4) respond with empathy and light follow-up questions. Keep "
    "the conversation going naturally and do not end it abruptly."
)

# 各 preset = 言語 + STT/TTS/LLM + スピーカー2体。speaker[0] が口火 (kickoff)、[1] が応答役。
# persona=None なら bot.py の既定ペルソナ (= 日本語「あい」) を使う。
PRESETS: dict = {
    "ja": {
        "language": "ja",
        "stt_model": "nova-2",
        "tts_model": "eleven_multilingual_v2",
        "anthropic_model": "claude-sonnet-4-6",
        "speakers": [
            {
                "name": "あい",
                "persona": None,  # bot.py 既定 (あい)
                "voice": AI_VOICE_ID,
                "scenario": None,
                "kickoff": True,
                "kickoff_prompt": "まず一言で挨拶して、相手に自然に話しかけて。",
            },
            {
                "name": "ゆう",
                "persona": YUU_PERSONA,
                "voice": YUU_VOICE_ID,
                "scenario": YUU_SCENARIO,
                "kickoff": False,
                "kickoff_prompt": "",
            },
        ],
    },
    "en": {
        "language": "en",
        "stt_model": "nova-2",
        "tts_model": "eleven_multilingual_v2",
        "anthropic_model": "claude-sonnet-4-6",
        "speakers": [
            {
                "name": "Alex",
                "persona": ALEX_PERSONA,
                "voice": "0S5oIfi8zOZixuSj8K6n",
                "scenario": None,
                "kickoff": True,
                "kickoff_prompt": "Greet briefly and start a natural conversation.",
            },
            {
                "name": "Sam",
                "persona": SAM_PERSONA,
                "voice": "ZSNL4hPqCnqoMPaI4jGX",
                "scenario": SAM_SCENARIO,
                "kickoff": False,
                "kickoff_prompt": "",
            },
        ],
    },
}


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
            "STT_MODEL": preset["stt_model"],
            "TTS_MODEL": preset["tts_model"],
            "ANTHROPIC_MODEL": preset["anthropic_model"],
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
    args = ap.parse_args()

    for t in ("deepgram", "anthropic", "elevenlabs", "daily"):
        if not _tok(t):
            raise SystemExit(f"~/.{t}_token が無い")

    preset = json.loads(json.dumps(PRESETS[args.preset]))  # deep copy (voice 上書き用)
    spk = preset["speakers"]
    if args.voice_a:
        spk[0]["voice"] = args.voice_a
    if args.voice_b:
        spk[1]["voice"] = args.voice_b

    room = create_room(_tok("daily"))
    print(f"[room] {room}\n  (聴衆として聴くにはこの URL をブラウザで開く)")
    names = " / ".join(f"{s['name']}({s['voice'][:6]}…)" for s in spk)
    print(f"[preset] {args.preset}: {names}  lang={preset['language']}")

    stamp = int(time.time())
    base = Path(args.out_dir) / f"ai-conv-{args.preset}-{stamp}"
    a_pcm, b_pcm = f"{base}-a.pcm", f"{base}-b.pcm"
    t0 = time.time()  # 両ボット共通の録音開始基準

    env_a = speaker_env(preset, spk[0], room, a_pcm, t0)
    env_b = speaker_env(preset, spk[1], room, b_pcm, t0)

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
