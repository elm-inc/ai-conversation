# ADR-0003: Pipecat をパイプライン基盤に採用し Pipecat Cloud + Daily でホストする

## ステータス

採択 (2026-06-05)

ADR-0002 の「Pipecat は `AudioTransport` ポート裏のトランスポート専用」という位置づけを
**部分的に supersede** する（ポート抽象の思想自体は維持）。

## 文脈

[ADR-0001](0001-cascaded-orchestration-architecture.md) / [ADR-0002](0002-ports-and-adapters.md) に基づき、
自作オーケストレータ + ports & adapters で Phase 0-1 を実装した（endpointing / 融合ターン検出 /
フィラー / barge-in、実機ライブ疎通済み）。次の段階は **ブラウザから全二重で実機検証** すること。

リアルタイム音声のブラウザ配信には WebRTC が要る。自前でやると UDP・NAT 越え・TURN・エッジ分散の
運用が重い。検討の結果:

- **Railway 等の一般 PaaS は公開 UDP 受信が弱く、SmallWebRTC は TURN 必須**で成立が難しい。
- **メディアをマネージド（Daily）に逃がすと**ホスティングの UDP 制約が消え、コンピュートは可搬になる。
- Pipecat は Daily 製で、`DailyTransport`（サーバが outbound 参加）と **Pipecat Cloud**（Pipecat
  エージェント専用のマネージド実行基盤：セッション起動・オートスケール・観測性）が一級サポート。
- Pipecat はパイプライン（`transport.input → stt → llm → tts → transport.output`）が中核で、
  ターン検出やフィラーは **FrameProcessor** として差し込むのが流儀。自前トランスポートをポート裏に
  押し込むより、パイプラインを採用して差別化ロジックを Processor 化する方が摩擦が小さい。

## 決定

- **ホスティング = Pipecat Cloud（Daily）。トランスポート = DailyTransport。**
- **Pipecat のパイプラインを基盤として採用**し、STT/LLM/TTS は Pipecat の
  Deepgram/Anthropic/ElevenLabs サービスを使う（同じプロバイダのラッパ）。
- **我々の差別化資産はそのまま流用**する:
  - 純粋ロジック `aiconv.core.endpointing` / `aiconv.adapters.turn_fusion`（日本語の発話完結度・
    相槌/割り込み分類）は **`JapaneseEndpointingProcessor`（FrameProcessor）** として再利用。
  - フィラー / 構造化ペルソナ / 長期記憶も Processor / サービス設定として段階的に載せる。
- 自作 `ConversationOrchestrator` の配管（FSM・キュー barge-in）は Pipecat の native 割り込み
  （`allow_interruptions` + VAD）に置換する。`aiconv` ライブラリ（純ロジック）は維持。

## 理由

- **Railway/WebRTC の詰みを回避**: Daily が outbound 接続でメディアを肩代わり。TURN 不要。
- **Pipecat Cloud = 音声エージェント専用のマネージド基盤**: 1通話=1ボットのセッション起動・
  スケール・観測性を自作せずに済む。コードは可搬で、自前ホストへ戻すのも容易。
- **車輪の再発明をしない**（ADR-0001 の精神）: リアルタイム配管は枯れた Pipecat に任せ、価値は
  日本語ターンテイキング + 声優ペルソナ + 記憶（＝我々の Processor）に集中する。
- ADR-0002 の **ports & adapters の思想は維持**: 差別化ロジックは Pipecat 非依存の純 Python
  （`aiconv.core`）に保ち、Processor は薄いアダプタとして包むだけ。ベンダー差し替え余地を残す。

## 検討した代替案

### SmallWebRTCTransport + 自前ホスト（Railway/Fly）
- Pros: ベンダー非依存の自前メディア。
- Cons: Railway は UDP ingress 不可 → TURN 必須。Fly はネイティブ UDP だが運用負荷増。
- 不採用理由: 検証を急ぐ段でメディア運用を背負うのは非効率。Daily に逃がす方が圧倒的に軽い。

### WebSocket 音声トランスポート + 自前ホスト
- Pros: TURN 不要、どこでも動く。
- Cons: 音声フレーミング自作・WebRTC比で遅延増・AEC/VAD を自前で。
- 不採用理由: Daily が使えるなら WebRTC 品質を捨てる理由がない。

### Pipecat ネイティブのまま（自作ロジックを使わない）
- Pros: 最速で動く。
- Cons: 日本語ターンテイキングという差別化が消える。
- 不採用理由: 価値の源泉を捨てることになる。Processor 化で両立する。

## 帰結

### Pros
- ブラウザ全二重を最短で実機検証でき、スケール・運用は Pipecat Cloud が担う。差別化ロジックは維持。

### Cons
- Daily / Pipecat Cloud への依存（コスト・ベンダーロック）。`aiconv.core` を純ロジックに保つことで緩和。
- 自作オーケストレータの配管部分は使われなくなる（純ロジックは Processor で生きる）。

### 実機検証 / 将来の検討事項
- `JapaneseEndpointingProcessor` を観測段階から実ターン制御へ接続する。
- フィラーによるレイテンシ隠蔽・構造化ペルソナ・長期記憶 (Phase 2-3) を Processor 化して載せる。
- 本番スケール時の Pipecat Cloud 設定（min/max agents・region）チューニング。

### 関連 ADR
- [ADR-0001](0001-cascaded-orchestration-architecture.md), [ADR-0002](0002-ports-and-adapters.md)
