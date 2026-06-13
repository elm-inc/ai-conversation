"""AivisSpeech によるリアルタイム AI 議論デモ (発話駆動の即興討論)。

2 体のエージェント (技術者 × 倫理/人文の論者) が、与えられた論題について **発話で会話**する。
台本は事前に決めず、**直前の相手の発話を受け取ってから次の応答を生成し、AivisSpeech で合成して
発声する**——を 1 ターンずつ逐次に回す (record_text の「全ターン生成 → 一括合成」とは異なり、
受領 → 思考 → 発声 を交互にインターリーブする)。建設的議論のため、論題の事実知識を
expand_theme で 1 回だけ LLM に抽出させ、両者の system に注入する。

    uv run python live_debate.py                       # 既定論題で 8 ターン
    uv run python live_debate.py --theme "原発は脱炭素の解か" --turns 10
    uv run python live_debate.py --model claude-opus-4-8   # 推論品質を上げる
    uv run python live_debate.py --no-audio            # 発声せず録音だけ作る

前提:
- AivisSpeech Engine が起動していること (既定 http://127.0.0.1:10101)。/speakers で話者 id を確認。
- ~/.anthropic_token (応答生成と知識抽出に使用)。
- 発声は pw-play / aplay があれば即時再生する。音声デバイスが無い環境では自動で録音のみに退避。

出力: 各ターンの発話をライブで標準出力に表示しつつ、全体を 1 本の stereo WAV
(L=技術者 / R=倫理) に時刻整列で保存する (デバイスが無くても後で聴ける)。

設計上の位置づけ: STT を介さずテキストで発話内容を受け渡す (相手の「発話=その内容」を受領して
応答する)。STT 経由の誤認識は議論の整合性を壊すため (設計 conversation-tester.md)、知識集約の
討論ではテキスト受け渡しを採る。真の音響ループ (mic/STT) は将来の拡張余地。
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import os
import shutil
import subprocess
import tempfile
import time
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from director import _tok
from presets import SR  # stereo WAV のサンプルレート (24000)
from record_conversation import expand_theme  # テーマ知識の抽出 (DRY)
from record_text import _anthropic  # Anthropic REST 呼び出し (DRY)

from aiconv.adapters._engines.resample import resample_pcm16
from aiconv.adapters.tts_aivis import AivisSpeechTTS

DEFAULT_ENGINE_URL = "http://127.0.0.1:10101"  # AivisSpeech (VOICEVOX は 50021)
DEFAULT_THEME = "生成AIは人間の創造性を拡張するか、それとも代替するか"
PCM_SR = 16_000  # AivisSpeechTTS アダプタ出力 (core 規約 16kHz mono)
GAP_S = 0.5  # ターン間の無音 (発話の間)
PAN_NEAR, PAN_FAR = 0.85, 0.5  # gentle pan (両耳に出しつつ左右に寄せる)

# --- 議論の作法 (両者共通、読み上げ前提の制約込み) ---
DEBATE_STYLE = (
    "あなたは知的で誠実な討論者です。以下を必ず守ってください。"
    "まず直前の相手の発話の要点を一言受け止めてから、自分のレンズで論点を一つだけ前に進めます。"
    "建設的に——相手を論破するのではなく、誇張は正し、妥当な指摘は認め、議論を深めます。"
    "ただし安易に全面同意して話を畳まず、異なる角度から検討を加えます。"
    "事実を捏造せず、参考知識を踏まえます。分からない点は曖昧にせず、その旨を述べます。"
    "毎ターン質問で締めくくらず、立場・根拠・含意を述べて終える番も作ります。"
    "読み上げられるので、絵文字・記号・箇条書き・URL・かっこ書きの補足は出さず、"
    "話し言葉の自然な日本語にします。専門用語は噛み砕いて使います。"
    "発話は 2〜3 文・合計 150 字程度までに必ず収め、一度に持ち出す論点は一つだけにします。"
    "長い独白や畳みかけは避け、文の途中で切らず必ず言い切って終えます。"
)

# --- 2 人の論者 (異なる専門レンズ。voice は AivisSpeech 話者 id) ---
ENGINEER = {
    "name": "テックリードの澪",
    "lens": "AI 技術者の視点 (生成モデルの仕組み・能力・実装ワークフロー)",
    "speaker": 888753760,  # まお ノーマル
    "persona": (
        "あなたは生成 AI を実装してきたエンジニア『澪』です。拡散モデルや大規模言語モデルが"
        "潜在空間の中で既存データをどう再結合して出力を作るか、学習データ依存やモード崩壊、"
        "ファインチューニングやプロンプト設計といった実装の現実を具体的に理解しています。"
        "創作の現場で AI が『道具』としてどう制作プロセスを変えるか——下書き生成・反復・"
        "ヴァリエーション探索——を、能力と限界の両面から冷静に語ります。技術を過大評価も"
        "過小評価もせず、相手の倫理的懸念は技術的事実で受け止めて論点を具体化します。"
    ),
}
ETHICIST = {
    "name": "文化批評の灯",
    "lens": "倫理・人文の視点 (作者性・著作権・労働・文化的価値)",
    "speaker": 1878365376,  # コハク ノーマル
    "persona": (
        "あなたは芸術と技術の倫理を論じる批評家『灯』です。オリジナリティとは何か、作者性"
        "(authorship)や帰属、学習データと著作権、クリエイターの労働と雇用、文化にとっての"
        "創作の意味——こうした問いを専門に考えてきました。技術を頭ごなしに否定はせず、"
        "『技術的に可能であること』と『意味・権利・社会の面で望ましいこと』を切り分けて問います。"
        "技術者の説明で事実認識を更新しつつ、能力の話に回収されない価値の論点を粘り強く差し出します。"
    ),
}
SPEAKERS = [ENGINEER, ETHICIST]


class Speaker:
    """合成済み PCM を即時発声する。音声デバイスが無ければ一度だけ警告し以降は無効化する。"""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.cmd0: str | None = None
        if enabled:
            for c in ("pw-play", "aplay"):
                if shutil.which(c):
                    self.cmd0 = c
                    break
            if self.cmd0 is None:
                self.enabled = False
                print("[audio] pw-play / aplay が無い — 録音のみで続行します")

    def play(self, pcm: bytes, sr: int) -> None:
        if not self.enabled or self.cmd0 is None:
            return
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
            f.write(pcm)
            path = f.name
        try:
            if self.cmd0 == "pw-play":
                cmd = ["pw-play", "--rate", str(sr), "--format", "s16", "--channels", "1", path]
            else:
                cmd = ["aplay", "-q", "-r", str(sr), "-f", "S16_LE", "-c", "1", path]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0:
                self.enabled = False
                print("[audio] 音声出力デバイスに接続できない — 以降は録音のみで続行します")
        finally:
            os.unlink(path)


def _synth(adapter: AivisSpeechTTS, text: str) -> bytes:
    """AivisSpeech アダプタで 1 発話をまとめて合成し 16kHz mono PCM を返す (L0/L1 整形込み)。"""

    async def _collect() -> bytes:
        async def chunks() -> AsyncIterator[str]:
            yield text

        buf = bytearray()
        async for frame in adapter.synthesize(chunks()):
            buf += frame.data
        return bytes(buf)

    return asyncio.run(_collect())


def _system(sp: dict, theme: str, brief: str) -> str:
    parts = [
        sp["persona"],
        DEBATE_STYLE,
        f"# 今日の論題\n{theme}",
        f"# あなたの専門レンズ\n{sp['lens']}。このレンズを最後まで一貫させ、"
        "相手とは異なる角度から論点を出してください。",
    ]
    if brief:
        parts.append(f"# 参考知識 (事実として踏まえる。取り違えない)\n{brief}")
    return "\n\n".join(parts)


def _user(
    sp: dict, history: list[tuple[str, str]], theme: str, is_open: bool, is_last: bool
) -> str:
    if is_open:
        return (
            f"あなたが議論の口火を切ります。論題「{theme}」について、"
            f"{sp['lens']}の立場から基本的な見解と根拠を 2〜3 文で簡潔に述べ、"
            "最後に相手 (異なる専門の論者) へ論点を一つ渡してください。名前ラベルは付けない。"
        )
    transcript = "\n".join(f"{w}: {t}" for w, t in history)
    base = (
        f"これまでの議論:\n{transcript}\n\n"
        f"あなたは「{sp['name']}」({sp['lens']}) です。直前の相手の発話をまず一言受け止めてから、"
        "あなたのレンズで論点を一つだけ前に進めてください。2〜3 文・150 字程度で言い切り、"
        "名前ラベルは付けない。"
    )
    if is_last:
        base += (
            "\nこれが最後の発言です。ここまでの議論を踏まえ、建設的な着地点や"
            "なお残る論点を簡潔に述べて締めくくってください。"
        )
    return base


def _assemble_wav(
    segments: list[tuple[int, bytes]], speakers: list[dict], out: str
) -> None:
    """各ターンの 16kHz PCM を時刻整列して 1 本の stereo WAV (L=話者0 / R=話者1) に書く。"""
    seg = [(idx, resample_pcm16(pcm, PCM_SR, SR)) for idx, pcm in segments]
    chans = [bytearray(), bytearray()]
    cursor = 0.0
    for idx, pcm in seg:
        for c in range(2):  # 全チャンネルを cursor まで無音で埋める
            need = int(cursor * SR) * 2 - len(chans[c])
            if need > 0:
                chans[c] += b"\x00\x00" * (need // 2)
        chans[idx] += pcm
        cursor += len(pcm) / 2 / SR + GAP_S
    n = max(len(chans[0]), len(chans[1]))
    for c in range(2):
        chans[c] += b"\x00\x00" * ((n - len(chans[c])) // 2)
    nmin = min(len(chans[0]), len(chans[1]))

    def _norm(mono: bytes, target: float = 0.7) -> bytes:  # 話者ごとの音量差を揃える
        peak = audioop.max(mono, 2) or 1
        return audioop.mul(mono, 2, (target * 32767) / peak)

    la, lb = _norm(bytes(chans[0][:nmin])), _norm(bytes(chans[1][:nmin]))
    stereo = audioop.add(
        audioop.tostereo(la, 2, PAN_NEAR, PAN_FAR),
        audioop.tostereo(lb, 2, PAN_FAR, PAN_NEAR),
        2,
    )
    with wave.open(out, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(stereo)
    mb = Path(out).stat().st_size / 1_000_000
    pan = f"L={speakers[0]['name']}/R={speakers[1]['name']}"
    print(f"\n[recording] 保存: {out} ({mb:.1f} MB, {pan})")


def main() -> None:
    ap = argparse.ArgumentParser(description="AivisSpeech リアルタイム AI 議論デモ")
    ap.add_argument("--theme", default=DEFAULT_THEME, help="論題 (既定: 生成AIと創造性)")
    ap.add_argument("--turns", type=int, default=8, help="総ターン数 (交互。既定 8)")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="応答生成 LLM (既定 sonnet)")
    ap.add_argument("--engine-url", default=DEFAULT_ENGINE_URL, help="AivisSpeech Engine URL")
    ap.add_argument(
        "--speakers", default=f"{ENGINEER['speaker']},{ETHICIST['speaker']}",
        help="話者 id ペア A,B (技術者,倫理)。/speakers で確認",
    )
    ap.add_argument("--out-dir", default="/tmp", help="WAV の出力先")
    ap.add_argument("--no-enrich", action="store_true", help="テーマ知識の事前注入を行わない")
    ap.add_argument("--no-audio", action="store_true", help="発声せず録音だけ作る")
    ap.add_argument("--max-tokens", type=int, default=400, help="1 発話あたりの最大トークン")
    args = ap.parse_args()

    akey = _tok("anthropic")
    if not akey:
        raise SystemExit("~/.anthropic_token が無い")
    if args.turns < 1:
        raise SystemExit("--turns は 1 以上")

    ids = [int(x) for x in args.speakers.split(",") if x.strip()]
    if len(ids) != 2:
        raise SystemExit("--speakers は A,B の 2 つの話者 id")
    speakers = [dict(SPEAKERS[0], speaker=ids[0]), dict(SPEAKERS[1], speaker=ids[1])]

    adapters = [AivisSpeechTTS(base_url=args.engine_url, speaker=s["speaker"]) for s in speakers]
    # 起動疎通を先に確認し、未起動なら案内して終わる (各ターンで初めて落ちるのを避ける)。
    err = adapters[0]._client.check()
    if err:
        raise SystemExit(f"AivisSpeech Engine に接続できない: {err}")

    brief = ""
    if not args.no_enrich:
        print(f"[enrich] 論題「{args.theme}」の知識を抽出中...")
        _, brief = expand_theme(args.theme, "ja")
        msg = f"参考知識 {len(brief)} 字を両者に注入" if brief else "知識なしで続行"
        print(f"[enrich] {msg}")

    out_speaker = Speaker(enabled=not args.no_audio)
    stamp = int(time.time())
    out = str(Path(args.out_dir) / f"ai-debate-{stamp}.wav")

    print(f"\n=== 論題: {args.theme} ===")
    print(f"  {speakers[0]['name']} ({speakers[0]['lens']})")
    print(f"  {speakers[1]['name']} ({speakers[1]['lens']})")
    audio_on = "on" if out_speaker.enabled else "off"
    print(f"  model={args.model} turns={args.turns} audio={audio_on}\n")

    history: list[tuple[str, str]] = []
    segments: list[tuple[int, bytes]] = []
    for turn in range(args.turns):
        idx = turn % 2
        sp = speakers[idx]
        is_open, is_last = turn == 0, turn == args.turns - 1
        print(f"[{turn + 1}/{args.turns}] {sp['name']} が考えています...", flush=True)
        t0 = time.time()
        line = _anthropic(
            _system(sp, args.theme, brief),
            _user(sp, history, args.theme, is_open, is_last),
            args.model, akey, max_tokens=args.max_tokens,
        )
        think_s = time.time() - t0
        history.append((sp["name"], line))
        print(f"  💬 {sp['name']}: {line}  ({think_s:.1f}s)")
        pcm = _synth(adapters[idx], line)
        out_speaker.play(pcm, PCM_SR)  # デバイスがあれば即時発声 (ブロッキング = 自然な間)
        segments.append((idx, pcm))

    _assemble_wav(segments, speakers, out)
    print(f"  base={out}")


if __name__ == "__main__":
    main()
