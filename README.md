# ai-conversation

生成音声によるキャラクター対話・コンパニオンサービス。
技術的ブレイクスルーは音声生成ではなく **「人の発話 → AI判定 → 自然な応答」の対話制御層** に置く。

- 設計: [docs/design/ai-conversation.md](docs/design/ai-conversation.md)
- ADR-0001: [cascaded + 自作オーケストレーション層](docs/adr/0001-cascaded-orchestration-architecture.md)
- ADR-0002: [ports & adapters 抽象化](docs/adr/0002-ports-and-adapters.md)

## Phase 0 — ポート定義 + PoC cascaded ループ (AIC-1)

ベンダー非依存の安定コア (`core/`) と交換可能なアダプタ (`adapters/`) を分離する境界を確定し、
mock アダプタでオフライン動作する cascaded ループ + レイテンシ harness を用意した。

```
src/aiconv/
  core/
    events.py        # 内部標準イベント型 (AudioFrame / Transcript / TokenChunk / TurnDecision ...)
    ports.py         # 交換可能なポート (STT/TTS/LLM/TurnDetector/Memory/Embedding/AudioTransport)
    orchestrator.py  # FSM (LISTEN/THINK/SPEAK/IDLE)。★応答音声は終端確定後のみ出力
    metrics.py       # レイテンシ harness (end_of_speech → first_audio)
  adapters/
    mock.py          # オフライン PoC 用 mock 一式
    turn.py          # 素朴な無音ターン検出 (Phase 1 で semantic 化)
    stt_deepgram.py  # Deepgram v7 live (健全性ゲート付き)
    llm_claude.py    # Claude streaming + prompt caching
    tts_elevenlabs.py# ElevenLabs Flash (声優ボイス + 許諾ガード/監査ログ)
    transport_wav.py # ローカル WAV トランスポート (実ベースライン計測用)
    transport_pipecat.py  # 本番 WebRTC (front-end 完成後に実装)
  poc/
    run_loop.py      # mock で end-to-end (オフライン)
    run_real.py      # 実プロバイダで end-to-end + 実ベースライン遅延
```

実アダプタは SDK を遅延 import するので、`--extra providers` 無しでもコアと mock は動く。

### セットアップ & 実行 (uv)

```bash
uv sync                              # dev 依存を解決
uv run python -m aiconv.poc.run_loop # PoC ループ (mock, オフライン)
uv run pytest                        # テスト
uv run ruff check . && uv run mypy   # lint + 型
```

### 実プロバイダで実ベースライン計測

```bash
uv sync --extra providers
export ANTHROPIC_API_KEY=... DEEPGRAM_API_KEY=... ELEVENLABS_API_KEY=...
uv run python -m aiconv.poc.run_real --in input.wav --out reply.wav --voice <ELEVENLABS_VOICE_ID>
```

声優音源を使うため `run_real` は `VoiceLicense` (許諾範囲) と `AuditSink` (監査ログ) を必ず通す。
本番 WebRTC は `transport_pipecat` を front-end 完成後に実装する。

### 設計上の不変条件

応答音声は **発話終端 (TurnLabel.COMPLETE) 確定後のみ** 出力する。投機生成 (LLM ドラフト) は
将来前倒ししてよいが、TTS への送出 = 発声は必ず終端確定ゲートを通す
(設計レビュー反映: 確定前の投機音声をスピーカーに出さない)。
