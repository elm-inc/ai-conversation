# voice-agent — Pipecat Cloud 日本語キャラクター対話 (MVP)

Daily 全二重でブラウザから話せる日本語キャラクター「あい」。Pipecat パイプライン上に
自作の日本語ターンテイキング (`aiconv.adapters.turn_fusion`) を FrameProcessor で載せる。

- 方針: [ADR-0003](../../docs/adr/0003-pipecat-cloud-pipeline.md)（Pipecat=パイプライン基盤+Daily、自作=Processor）
- パイプライン: `Daily.input → DeepgramSTT(ja) → JapaneseEndpointingProcessor(観測) → user_agg → AnthropicLLM(persona) → ElevenLabsTTS(声優voice) → Daily.output → assistant_agg` + Silero VAD

## 必要なもの
- Pipecat Cloud アカウント（作成済）／Daily（Pipecat Cloud に内包）
- API キー: `DEEPGRAM_API_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY`
- 声優の `ELEVENLABS_VOICE_ID`

## デプロイ手順（runbook）

```bash
# 0. CLI 認証
pip install pipecatcloud            # or: uv tool install pipecatcloud
pcc auth login

# 1. シークレットセット作成（キーは Pipecat Cloud 側に保管）
pcc secrets set ai-conversation-secrets \
  DEEPGRAM_API_KEY=... ANTHROPIC_API_KEY=... ELEVENLABS_API_KEY=... \
  ELEVENLABS_VOICE_ID=c2XJrw7TvNGtOc6r0ijG

# 2. イメージをビルド&push（build context はリポジトリルート）
#    pcc docker のヘルパ、または素の docker:
docker build -f apps/voice-agent/Dockerfile -t <REGISTRY>/ai-conversation-voice:0.1 .
docker push <REGISTRY>/ai-conversation-voice:0.1
#    → pcc-deploy.toml の image を push したタグに更新

# 3. デプロイ（pcc-deploy.toml を参照）
cd apps/voice-agent
pcc deploy

# 4. ブラウザで検証
#    Pipecat Cloud ダッシュボードの sandbox からセッション開始 → Daily ルームが開く。
#    マイクを許可して「あい」と話す。全二重で割り込み（barge-in）も試す。
```

## ローカル開発（Pipecat Cloud 前に手元で確認）
Pipecat の dev runner で Daily ルームに対して直接起動できる（`DAILY_API_KEY` が必要）。
詳細は Pipecat の `pipecat.runner` ドキュメント参照。`bot()` はそのまま使い回せる。

## 既知の未確定点（初回デプロイで確認・調整）
- ベースイメージ `dailyco/pipecat-base` のタグ、`pcc-deploy.toml` のキー名は Pipecat Cloud の
  最新ドキュメントに合わせて確認する（CLI フラグ: `--secrets` / `--min-agents` / `--max-agents`）。
- `JapaneseEndpointingProcessor` は MVP では「観測+ログ」。実ターン制御への接続は次段。
