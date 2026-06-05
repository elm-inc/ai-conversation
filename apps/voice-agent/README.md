# voice-agent — Japanese character voice agent "あい" (Pipecat Cloud + Daily)

*[English](#english) | [日本語](#日本語)*

<a name="english"></a>

Browser full-duplex Japanese character "あい", on Pipecat Cloud + Daily. Follows the Pipecat CLI canonical template (`PipelineWorker` / `WorkerRunner` / `bot(runner_args)`). Policy: [ADR-0003](../../docs/adr/0003-pipecat-cloud-pipeline.md).

**Pipeline:** `Daily.input → DeepgramSTT(ja) → user_agg → AnthropicLLM(persona) → [Filler] → ElevenLabsTTS → Daily.output → assistant_agg`, with Silero VAD + Smart Turn v3 (semantic end-of-turn).

The same `bot.py` is **role-parameterized via env**, so it powers both production あい and the interlocutor used by `apps/conversation-tester`. With no env set it equals the あい default.

## Key env knobs (all optional; defaults = あい)

| Env | Default | Purpose |
|---|---|---|
| `ANTHROPIC_MODEL` / `TTS_MODEL` / `STT_MODEL` | haiku / multilingual_v2 / nova-2 | model selection |
| `PERSONA_PROMPT` / `AGENT_NAME` / `SCENARIO` | あい | role parameterization |
| `FILLER` / `FILLER_PROB` | off / 1.0 | latency-hiding backchannel ("えーと") |
| `VAD_STOP_SECS` / `TURN_MIN_WORDS` | 0.2 / 0 | turn-taking tuning (barge-in gating) |
| `PREWARM` | on | warm the LLM at startup (first-response latency) |
| `STT_KEYTERMS` / `KNOWLEDGE_BRIEF` | — | STT dictionary boost / grounding (theme expansion) |
| `RECORD` / `RECORD_TRACK` | off / mix | record the conversation (used by the tester) |

## Local development

```bash
cd apps/voice-agent
uv sync
cp .env.example .env          # keys + ELEVENLABS_VOICE_ID + DAILY_API_KEY
uv run bot.py --transport daily   # prints a Daily room URL → open in a browser
```

## Pipecat Cloud deploy

```bash
pipecat cloud auth login
# secrets (stored cloud-side)
pipecat cloud secrets set ai-conversation-voice-secrets \
  DEEPGRAM_API_KEY=… ANTHROPIC_API_KEY=… ANTHROPIC_MODEL=claude-haiku-4-5 \
  ELEVENLABS_API_KEY=… ELEVENLABS_VOICE_ID=… STT_LANGUAGE=ja STT_MODEL=nova-2 \
  TTS_MODEL=eleven_multilingual_v2 FILLER=1 FILLER_PROB=0.4
# arm64 image (dailyco/pipecat-base is arm64-only) → ghcr.io, then deploy
docker buildx build --platform linux/arm64 -t ghcr.io/elm-inc/ai-conversation-voice:<tag> --push .
pipecat cloud deploy ai-conversation-voice ghcr.io/elm-inc/ai-conversation-voice:<tag> \
  --credentials ghcr-pull --min-agents 1 --force
```

**Notes:** commit `uv.lock` (Dockerfile uses `uv sync --locked`). Rebuild the image only on `bot.py` changes; env-only tweaks are secret changes + redeploy. Lessons: prompt caching on a short persona (<1024 tok) 400s the API; change one Pipecat behavior at a time, deploy → verify.

---

<a name="日本語"></a>

# 日本語

ブラウザ全二重で話せる日本語キャラクター「あい」。Pipecat Cloud + Daily。Pipecat CLI の正準テンプレート（`PipelineWorker` / `WorkerRunner` / `bot(runner_args)`）準拠。方針: [ADR-0003](../../docs/adr/0003-pipecat-cloud-pipeline.md)。

**パイプライン:** `Daily.input → DeepgramSTT(ja) → user_agg → AnthropicLLM(persona) → [Filler] → ElevenLabsTTS → Daily.output → assistant_agg` + Silero VAD + Smart Turn v3（意味的な発話終端判定）。

同じ `bot.py` を **env で役割パラメータ化**しており、本番「あい」と `apps/conversation-tester` の対話相手の両方を生やす。env 未設定なら あい既定と一致。

## 主な env ノブ（すべて任意・既定=あい）

| Env | 既定 | 用途 |
|---|---|---|
| `ANTHROPIC_MODEL` / `TTS_MODEL` / `STT_MODEL` | haiku / multilingual_v2 / nova-2 | モデル選択 |
| `PERSONA_PROMPT` / `AGENT_NAME` / `SCENARIO` | あい | 役割パラメータ化 |
| `FILLER` / `FILLER_PROB` | off / 1.0 | レイテンシ隠蔽の相づち（「えーと」）|
| `VAD_STOP_SECS` / `TURN_MIN_WORDS` | 0.2 / 0 | ターンテイキング調整（割り込みゲート）|
| `PREWARM` | on | 起動時に LLM を温める（初回応答遅延）|
| `STT_KEYTERMS` / `KNOWLEDGE_BRIEF` | — | STT 辞書ブースト / グラウンディング（テーマ展開）|
| `RECORD` / `RECORD_TRACK` | off / mix | 会話録音（テスターが使用）|

## ローカル開発

```bash
cd apps/voice-agent
uv sync
cp .env.example .env          # キー + ELEVENLABS_VOICE_ID + DAILY_API_KEY
uv run bot.py --transport daily   # Daily ルーム URL が出る → ブラウザで開く
```

## Pipecat Cloud デプロイ

```bash
pipecat cloud auth login
# シークレット（クラウド側保管）
pipecat cloud secrets set ai-conversation-voice-secrets \
  DEEPGRAM_API_KEY=… ANTHROPIC_API_KEY=… ANTHROPIC_MODEL=claude-haiku-4-5 \
  ELEVENLABS_API_KEY=… ELEVENLABS_VOICE_ID=… STT_LANGUAGE=ja STT_MODEL=nova-2 \
  TTS_MODEL=eleven_multilingual_v2 FILLER=1 FILLER_PROB=0.4
# arm64 イメージ（dailyco/pipecat-base は arm64 のみ）→ ghcr.io → deploy
docker buildx build --platform linux/arm64 -t ghcr.io/elm-inc/ai-conversation-voice:<tag> --push .
pipecat cloud deploy ai-conversation-voice ghcr.io/elm-inc/ai-conversation-voice:<tag> \
  --credentials ghcr-pull --min-agents 1 --force
```

**注記:** `uv.lock` をコミット（Dockerfile は `uv sync --locked`）。再ビルドは `bot.py` 変更時のみ、env だけの調整は secret 変更 + 再デプロイ。教訓: 短い persona(<1024tok)への prompt caching は API 400／Pipecat の挙動変更は 1 つずつ deploy→検証。
