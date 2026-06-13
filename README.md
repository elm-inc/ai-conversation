# ai-conversation

*[English](#english) | [日本語](#日本語)*

<a name="english"></a>

A generated-voice character-conversation / companion service. The technical breakthrough is **not** voice generation but the **orchestration layer**: *human speech → AI judgment → natural response* — low latency, intent-aware turn-taking, and a consistent personality.

- Design: [docs/design/ai-conversation.md](docs/design/ai-conversation.md)
- ADR-0001: [cascaded + custom orchestration layer](docs/adr/0001-cascaded-orchestration-architecture.md)
- ADR-0002: [ports & adapters](docs/adr/0002-ports-and-adapters.md)
- ADR-0003: [Pipecat Cloud + Daily pipeline](docs/adr/0003-pipecat-cloud-pipeline.md)

## Repository layout

```
src/aiconv/              # Vendor-neutral orchestration core (ports & adapters)
  core/                  #   events / ports / orchestrator(FSM) / endpointing / metrics
  adapters/              #   mock + real (Deepgram/Claude/ElevenLabs) + turn_fusion + filler
  poc/                   #   offline (mock) and real end-to-end loops + latency harness
apps/voice-agent/        # Deployed voice agent "あい" (Pipecat Cloud + Daily, browser full-duplex)
  bot.py                 #   Daily → DeepgramSTT(ja) → AnthropicLLM(persona) → ElevenLabsTTS → Daily
apps/conversation-tester/# AI-to-AI conversation subsystem (test harness + content)
  director.py            #   cloud あい + local interlocutor over Daily (real audio path)
  record_conversation.py #   dual-local high-quality recording (presets, themes, enrichment)
  record_text.py         #   text-level conversation (no STT, REST TTS) — highest coherence
  live_debate.py         #   AivisSpeech real-time reactive debate (2 experts, unscripted)
  judge.py               #   AI judge: rubric scoring of a recorded conversation
  presets.py             #   characters / languages / themes (single source)
docs/                    # ADRs, architecture, design
```

## Quick start

```bash
# Core PoC (offline, mock adapters)
uv sync
uv run python -m aiconv.poc.run_loop
uv run pytest && uv run ruff check . && uv run mypy

# Voice agent "あい" locally (needs DAILY/DEEPGRAM/ANTHROPIC/ELEVENLABS keys)
cd apps/voice-agent && uv sync && uv run bot.py --transport daily   # opens a Daily room URL

# AI-to-AI conversation + judge (tokens in ~/.{anthropic,deepgram,elevenlabs,daily}_token)
cd apps/conversation-tester
uv run python record_conversation.py --preset ja --theme "おすすめの映画"   # real audio path (with STT)
uv run python record_text.py        --preset en --theme "favorite movies"  # text-level (no STT, cleanest)
uv run python judge.py                                                      # score the latest recording

# Real-time reactive AI debate via AivisSpeech (needs AivisSpeech engine on :10101 + ~/.anthropic_token)
uv run python live_debate.py --theme "生成AIは人間の創造性を拡張するか" --turns 8  # each turn is generated after hearing the last
```

## Two recording modes

| Mode | Path | Use |
|---|---|---|
| `record_conversation.py` | real audio over Daily → STT → LLM → TTS | **regression-test the live voice pipeline** (STT errors are real) |
| `record_text.py` | text-level conversation → REST TTS | **content recordings** — no STT misrecognition, no streaming seams |

`judge.py` scores any recording (自然さ / 人格一貫性 / ターンテイキング / 整合性 / 遅延体感) and emits `regression_risk` for CI use.

## Design invariant

Response audio is spoken **only after the user's turn is confirmed complete**. Speculative LLM drafting may run earlier, but sending audio to TTS (= speaking) always passes the turn-confirmation gate.

---

<a name="日本語"></a>

# 日本語

生成音声によるキャラクター対話・コンパニオンサービス。技術的ブレイクスルーは音声生成そのものではなく、**「人の発話 → AI 判定 → 自然な応答」の対話制御層**（低遅延・意図を汲んだターンテイキング・一貫した人格）に置く。

- 設計: [docs/design/ai-conversation.md](docs/design/ai-conversation.md)
- ADR-0001: [cascaded + 自作オーケストレーション層](docs/adr/0001-cascaded-orchestration-architecture.md)
- ADR-0002: [ports & adapters 抽象化](docs/adr/0002-ports-and-adapters.md)
- ADR-0003: [Pipecat Cloud + Daily パイプライン](docs/adr/0003-pipecat-cloud-pipeline.md)

## リポジトリ構成

```
src/aiconv/              # ベンダー非依存の対話オーケストレーション core (ports & adapters)
  core/                  #   events / ports / orchestrator(FSM) / endpointing / metrics
  adapters/              #   mock + 実(Deepgram/Claude/ElevenLabs) + turn_fusion + filler
  poc/                   #   オフライン(mock)/実 の end-to-end ループ + レイテンシ harness
apps/voice-agent/        # 本番ボイスエージェント「あい」(Pipecat Cloud + Daily, ブラウザ全二重)
  bot.py                 #   Daily → DeepgramSTT(ja) → AnthropicLLM(persona) → ElevenLabsTTS → Daily
apps/conversation-tester/# AI 同士会話サブシステム (テストハーネス + コンテンツ)
  director.py            #   cloud あい + ローカル対話相手を Daily で同室 (実音声パス)
  record_conversation.py #   dual-local 高品質録音 (presets/テーマ/テーマ展開)
  record_text.py         #   テキストレベル会話 (STT なし, REST TTS) — 最も整合性が高い
  live_debate.py         #   AivisSpeech リアルタイム即興討論 (専門家2体, 台本なし・発話駆動)
  judge.py               #   AI judge: 録音会話のルーブリック採点
  presets.py             #   キャラ / 言語 / テーマ (単一ソース)
docs/                    # ADR / architecture / design
```

## クイックスタート

```bash
# core PoC (オフライン, mock)
uv sync
uv run python -m aiconv.poc.run_loop
uv run pytest && uv run ruff check . && uv run mypy

# ボイスエージェント「あい」をローカルで (DAILY/DEEPGRAM/ANTHROPIC/ELEVENLABS キーが必要)
cd apps/voice-agent && uv sync && uv run bot.py --transport daily   # Daily ルーム URL が出る

# AI 同士会話 + judge (トークンは ~/.{anthropic,deepgram,elevenlabs,daily}_token)
cd apps/conversation-tester
uv run python record_conversation.py --preset ja --theme "おすすめの映画"   # 実音声パス (STT あり)
uv run python record_text.py        --preset en --theme "favorite movies"  # テキストレベル (STT なし, 最もクリーン)
uv run python judge.py                                                      # 直近の録音を採点

# AivisSpeech によるリアルタイム即興討論 (AivisSpeech Engine :10101 と ~/.anthropic_token が必要)
uv run python live_debate.py --theme "生成AIは人間の創造性を拡張するか" --turns 8  # 相手の発話を受けてから応答を生成・発声
```

## 2 つの録音モード

| モード | 経路 | 用途 |
|---|---|---|
| `record_conversation.py` | Daily 実音声 → STT → LLM → TTS | **実音声パイプラインの回帰検証**（STT 誤認識も現実として含む）|
| `record_text.py` | テキストレベル会話 → REST TTS | **コンテンツ録音** — STT 誤認識なし・ストリーミング継ぎ目なし |

`judge.py` はどちらの録音も採点（自然さ / 人格一貫性 / ターンテイキング / 整合性 / 遅延体感）し、CI 用に `regression_risk` を返す。

## 設計上の不変条件

応答音声は **発話終端が確定した後のみ** 発声する。投機的な LLM ドラフトは前倒し可だが、TTS への送出（＝発声）は必ず終端確定ゲートを通す。
