# P0 実機実測 Runbook (AIC-8)

GPU 復帰後に **コピペで回す**ための手順。エンジン別の詳細・ライセンスは [README.md](README.md)、
設計は [docs/design/japanese-tts-optimization.md](../../docs/design/japanese-tts-optimization.md)。

**ゴール**: ESPnet / VOICEVOX / Kokoro / ElevenLabs を同一テストセットで合成し、
**自然さ・アクセント・TTFA/RTF/VRAM** を実測して**本命エンジンを確定**する → AIC-8 を閉じ、
P3 (AIC-14 本番統合) を解放する。

---

## 0. 前提 (Pre-flight) — ドライバと GPU 解放

> ⚠️ 2026-06-11 時点で NVIDIA カーネルモジュール (580.142) とユーザ空間ライブラリ (580.159) が
> 不一致で `nvidia-smi` が落ちていた。**新規 GPU プロセスはドライバ整合まで起動できない**。

```bash
# (1) ドライバ整合を確認 (再起動 or モジュール再ロード後)。これが通らなければ先に進まない。
nvidia-smi

# (2) GPU VRAM を空ける: 常駐 Qwen(vLLM) を停止 (要 sudo / プロンプトで `!` 実行)
#     ※ ESPnet VITS は数百 MB と小さく相乗りも可能だが、TTFA を綺麗に測るため停止推奨。
sudo systemctl stop vllm-qwen-coder
nvidia-smi   # VRAM が空いたか確認

# 実測後に Qwen を戻す: sudo systemctl start vllm-qwen-coder
# (再起動経由なら disable していた場合 enable --now で復帰)
```

---

## 1. 重要: 依存衝突のため **2 パス**で実測する

Kokoro の `misaki[ja]` は本家 `pyopenjtalk` に依存し、`tts-bench` extra の `pyopenjtalk-plus` と
**同一モジュール名で衝突**する (pyproject `[tool.uv] conflicts` で同時インストール禁止)。
→ **Pass A (ESPnet/VOICEVOX/ElevenLabs)** と **Pass B (Kokoro)** を別 sync で回し、結果を統合する。

---

## 2. Pass A — ESPnet (本命) + VOICEVOX + ElevenLabs

```bash
cd ~/repos/github.com/elm-inc/ai-conversation

# 依存導入 (pyopenjtalk-plus + ESPnet + ElevenLabs SDK)
uv sync --inexact --extra tts-bench --extra tts-bench-espnet --extra providers

# トークン (未設定なら)
echo "<ELEVENLABS_API_KEY>" > ~/.elevenlabs_token
echo "<ANTHROPIC_API_KEY>"  > ~/.anthropic_token   # judge 用

# VOICEVOX ENGINE をローカル起動 (別ターミナル / バックグラウンド)
docker run --rm -d -p 127.0.0.1:50021:50021 voicevox/voicevox_engine:cpu-latest
curl -s localhost:50021/version   # 起動確認

# GPU 推論を指定して実測 (初回は ESPnet モデル DL 数百 MB)
ESPNET_DEVICE=cuda \
  uv run python apps/tts-bench/run_bench.py \
    --engines elevenlabs,espnet,voicevox --judge \
    --out apps/tts-bench/out/passA
```

- 出力: `apps/tts-bench/out/passA/{report.md,results.csv,<engine>/<id>.wav}`
- VOICEVOX は L1 アクセント注入が効く (mora_data でピッチ再計算、句/モーラ一致時のみ)。
- 話者: `VOICEVOX_SPEAKER`(既定 3=ずんだもん)。あい/ゆう相当の声質候補を `GET /speakers` から選び直してもよい。

---

## 3. Pass B — Kokoro (即時汎用ボイス)

```bash
# tts-bench extra を外して kokoro を入れる (衝突回避。本家 pyopenjtalk が入りアクセントチェックは動く)
uv sync --inexact --extra tts-bench-kokoro
uv run python -m unidic download        # misaki[ja] 形態素辞書 (初回のみ ~1GB)

uv run python apps/tts-bench/run_bench.py \
  --engines kokoro --judge \
  --out apps/tts-bench/out/passB
```

実測後、開発環境を戻す: `uv sync --inexact --extra tts-bench --extra tts-bench-espnet`

---

## 4. 結果回収と判定

各 `out/passX/report.md` を突き合わせ、下記で**本命を 1 つ確定**する (現実的には ESPnet vs VOICEVOX の二択。
Kokoro/ElevenLabs は参照基準)。

| 観点 | 取得元 | 判定の目安 |
|---|---|---|
| **アクセント正確性** | report.md のアクセント表 + **wav を実聴** | 最小対立 (雨/飴 等) が正しく聞き分けられるか。フロントエンド層は既に句一致 100% なので、**音響実現**を耳で確認 |
| **自然さ** | report.md の**聴取シート (人手記入)** + `--judge` の読みサニティ | 平板・不自然な抑揚・音切れがないか |
| **TTFA / RTF** | results.csv | ⚠️ ESPnet/VOICEVOX は**非ストリーミング**のため TTFA=全合成時間。**RTF と1文 elapsed** で見て、目標 TTFA<300ms は P3 の文/節チャンク化前提で読み替える |
| **VRAM** | results.csv (`vram_peak_mb`) | vLLM(Qwen ~20GB) と同居可能か。専用 GPU/インスタンスが要るか |
| **ライセンス** | README.md ライセンス表 | ESPnet=要 base 規約 (JSUT 商用要連絡 / jvnv CC BY-SA)、VOICEVOX=キャラ規約+クレジット |

**実聴のやり方**: `apps/tts-bench/out/passA/espnet/*.wav` 等を再生し、report.md の聴取シートに自然さ/
アクセントを採点記入 (Anthropic API は音声入力非対応のため自然さは人手)。

---

## 5. 確定後のフィードバック

```text
1. AIC-8 にコメントで実測結果 (本命エンジン・TTFA/RTF/VRAM・ライセンス判断) を記録 → Done
2. 設計 §3 の「P0 で確定」を実測値で更新 (drift 防止)
3. AIC-14 (P3 本番統合) を本命エンジンで着手可能に。
   - ESPnet 勝利 → tts_espnet.py を Pipecat TTSService 化 (in-process)
   - AivisSpeech を tts_aivis (VOICEVOX互換) で。GPU 配置 (案A/B, 設計 §11) も併せて確定
4. Qwen を戻す: sudo systemctl start vllm-qwen-coder
```

---

## トラブルシュート (Runbook 固有)

- `nvidia-smi` が NVML mismatch → ドライバ未整合。再起動 or `modprobe -r/​modprobe nvidia` (要 sudo)。
- ESPnet が `CUDA error` / CPU に落ちる → `ESPNET_DEVICE=cuda` を渡したか、`nvidia-smi` が通るか確認。
- VOICEVOX 接続不可 → `curl localhost:50021/version`。docker が 50021 を listen しているか。
- Pass 切替で import エラー → A/B の `uv sync` を混在させていないか (conflicts)。各パスの sync をやり直す。
- 詳細・各エンジンの環境変数は [README.md](README.md) を参照。
