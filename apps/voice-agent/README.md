# voice-agent — Pipecat Cloud 日本語キャラクター対話 (MVP)

Daily 全二重でブラウザから話せる日本語キャラクター「あい」。Pipecat CLI の canonical
テンプレート構造に準拠 (`PipelineWorker` / `WorkerRunner` / `bot(runner_args)`)。

- 方針: [ADR-0003](../../docs/adr/0003-pipecat-cloud-pipeline.md)（Pipecat=パイプライン基盤+Daily）
- パイプライン (MVP): `Daily.input → DeepgramSTT(ja) → user_agg → AnthropicLLM(persona「あい」) → ElevenLabsTTS(声優voice) → Daily.output → assistant_agg` + Silero VAD
- ターンテイキングは MVP では Pipecat の VAD/割り込みに委譲。日本語 semantic endpointing
  (`processors.JapaneseEndpointingProcessor` / aiconv) の接続は次増分。

## 必要なもの
- Pipecat Cloud アカウント（作成済）
- API キー: `DEEPGRAM_API_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY`
- 声優の `ELEVENLABS_VOICE_ID`

## ローカル開発（まず手元で確認）
```bash
cd apps/voice-agent
uv sync
cp .env.example .env   # キーと ELEVENLABS_VOICE_ID を記入、DAILY_API_KEY も
uv run bot.py --transport daily   # Daily ルームが発行され URL が出る → ブラウザで開いて会話
```

## Pipecat Cloud デプロイ（`pipecat cloud` CLI）
```bash
pipecat cloud auth login                       # ブラウザ認証

# シークレットセット (キーはクラウド側に保管)
pipecat cloud secrets set ai-conversation-voice-secrets \
  DEEPGRAM_API_KEY=… ANTHROPIC_API_KEY=… ANTHROPIC_MODEL=claude-sonnet-4-6 \
  ELEVENLABS_API_KEY=… ELEVENLABS_VOICE_ID=c2XJrw7TvNGtOc6r0ijG \
  STT_LANGUAGE=ja STT_MODEL=nova-2 TTS_MODEL=eleven_flash_v2_5

# イメージ build & push（Pipecat Cloud のレジストリへ）
pipecat cloud docker build-push    # or: docker build -t <repo>:<tag> . && docker push …

# デプロイ (pcc-deploy.toml 参照)
pipecat cloud deploy

# → ダッシュボードの sandbox からセッション開始 → ブラウザで「あい」と会話・割り込みを試す
```

## 注記
- `uv.lock` をコミットしておくこと（Dockerfile の `uv sync --locked` が参照）。
- base image `dailyco/pipecat-base` のタグ・プロファイル名は Pipecat Cloud ドキュメントで最新確認。
- 次増分: `JapaneseEndpointingProcessor` を pipeline に接続（aiconv パッケージング込み）。
