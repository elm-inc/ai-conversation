# 生成音声AI対話サービス — 設計

- Status: Draft（DeepSeek-R1 レッドチーム反映済 / v2）
- Linear: (未起票)
- 関連 ADR: [ADR-0001](../adr/0001-cascaded-orchestration-architecture.md)（cascaded + 自作オーケストレーション層）, [ADR-0002](../adr/0002-ports-and-adapters.md)（ports & adapters 抽象化）
- 最終更新: 2026-06-03

> このドキュメントは「随時更新」の設計方針（design）。決定の理由（why）は確定後 `docs/adr/` に昇格する。

---

## Context（なぜこれを作るか）

「生成音声によるAIとの対話」をテーマにしたキャラクター対話・コンパニオンサービスの構想。
技術的ブレイクスルーを **音声生成そのものではなく、「人の発話 → AI判定 → 自然な応答」の対話制御層** に置く。
声（TTS）は権利クリアな声優音声 × ElevenLabs、STT も既存サービスを使う前提で、
**ゼロベースで構築が必要なのは中間のオーケストレーション層**。

「ブレイクスルー」の正体は次の3点に分解できる:

1. **低遅延** … 発話終了から応答音声開始まで体感 < 800ms
2. **発話意図を汲んだ正確な応答** … いつ話し終えたかを意味で判定し、割り込み/相槌を識別
3. **性格をもったAI** … 一貫した会話人格 + 長期記憶による関係性 + 自然な間

2026年時点の業界知見では、cascaded（STT→LLM→TTS）構成が依然プロダクション主流で、
speculative response generation / semantic turn detection / backchannel識別 が「自然な対話」の鍵。
本構想は「TTS/STT＝既存、対話エンジン＝自作」なので、まさに cascaded + 自作オーケストレーション層が最適解。

---

## 中核アーキテクチャ — Cascaded + 自作オーケストレーション層

```
マイク音声 (WebRTC streaming, full-duplex)
  │
  ▼
[1] ストリーミングSTT (日本語, partial transcript を逐次出力)
  │
  ▼
[2] ターンテイキング層 ★自作の核心  ── FSM: LISTEN / THINK / SPEAK / IDLE
  │    - semantic endpointing（発話完了を「意味」で予測、無音タイマー依存を脱却）
  │    - backchannel(相槌) vs barge-in(割り込み) vs 継続無音 の分類
  │
  ▼
[3] 意図理解 + 先読み応答生成 ★自作  ── speculative response generation
  │    - persona-conditioned LLM（性格・口調・価値観を注入、token streaming）
  │    - 長期記憶の検索注入（semantic memory への RAG）
  │
  ▼
[4] 文/節単位チャンク → ストリーミングTTS (ElevenLabs Flash, 声優ボイス)
  │
  ▼
スピーカー出力（barge-in 検出で即時停止・flush）
```

**なぜ cascaded か**: 声を「特定声優の権利クリア音源 × ElevenLabs」に固定する要件があるため、
native speech-to-speech（OpenAI Realtime 等、音声内蔵モデル）では独自TTS声に差し替えられない。
cascaded なら各段を自由に差し替えでき、テキスト段が露出するのでデバッグ・人格制御・記憶注入が可能。

---

## 抽象化レイヤー設計 ★前提要件（差し替え可能性を担保）

個々の外部サービス・モデル（STT/TTS/LLM/ターン検出/記憶）は **検証によって別物に差し替わりうる**。
そのため **ports & adapters（ヘキサゴナル）** を採用し、
**「対話オーケストレーションの中核ロジック＝ベンダー非依存の安定コア」「外部依存＝すべてポート背後の交換可能なエッジ」** に分離する。

### 安定コア（自作・ベンダー非依存）
- FSM ターンテイキング（LISTEN/THINK/SPEAK/IDLE）
- 対話マネージャ（意図統合・応答方針決定・先読み制御）
- ペルソナ適用ロジック・記憶ポリシー（要約/検索/関係状態の更新規則）

これらは**特定ベンダーのAPI仕様を一切知らない**。標準化した内部イベント型だけを扱う。

### 交換可能なポート（インターフェース）と現候補アダプタ

| Port（安定IF） | 責務（標準化した入出力） | 現候補アダプタ（差し替え可） |
|---|---|---|
| `STTProvider` | 音声フレーム → partial/final transcript（時刻・確信度付き） | Deepgram / AssemblyAI / Google / kotoba-whisper |
| `TTSProvider` | テキストchunk → 音声フレーム（interrupt/flush・voice選択対応） | ElevenLabs Flash（声優ボイス） |
| `LLMProvider` | messages+persona+cacheヒント → token stream（TTFT露出） | Claude系 等 |
| `TurnDetector` | (partial transcript, 音響特徴) → {complete/incomplete/backchannel/barge-in} 確率 | LiveKit turn detector / 自作日本語モデル |
| `MemoryStore` | エピソード要約の書込・top-k検索・関係状態 | vector DB ＋ 要約ストア |
| `EmbeddingProvider` | text → vector | （検索/記憶用、独立差し替え） |
| `AudioTransport` | WebRTC/WS の音声I/O | Pipecat / LiveKit Agents（※交換コストは高め） |

### 設計原則
- **標準データ型の固定**: 音声フレーム形式・transcriptイベント・tokenイベントを内部標準として定義。ベンダー差し替えがコアに波及しない
- **アンチコラプション**: 各アダプタがベンダー固有の癖（partialの不安定さ・遅延ばらつき・日本語特性・flush挙動）を吸収し、コアには正規化済みイベントだけ渡す。**ベンダー固有機能をコアに漏らさない**（例: ElevenLabsの音声機能はアダプタ設定に閉じる）
- **能力フラグ（capability）**: 各Providerが「partialストリーミング可」「barge-in flush遅延」等を宣言。コアは能力に応じてフォールバック
- **設定駆動の選択**: DI/factory でプロバイダを構成切替（コード変更なしでA/B）
- **ポート境界に計測フックを内蔵**: 同一IF背後で複数ベンダーをベンチ比較できる → 「検証で差し替える」運用が無摩擦に回る（後述の検証harnessと直結）

---

## レイテンシ予算（目標: 発話終了 → 応答音声開始 体感 < 800ms）

| 段 | 単純合算 | 最適化後の寄与 |
|---|---|---|
| STT 終端確定 (semantic endpoint) | 発話停止後 ~700ms（無音タイマー） | **~150-300ms**（意味で先行確定） |
| LLM TTFT | 400-800ms | **~200-400ms**（persona/記憶を prompt caching） |
| TTS TTFA (ElevenLabs Flash v2.5) | 300-500ms | **~150-300ms**（文単位ストリーミング） |
| 単純合計 | **1.5〜2s（NG）** | — |

**体感 < 800ms に落とす3つのレバー（自作の肝）:**
1. **オーバーラップ実行** — 各段をストリーミングし、STTのpartialが出た時点で[3]を走らせ、LLMの最初の文が出た時点でTTSへ流す（合算ではなく最長経路）
2. **先読み応答 (speculative)** — 発話終了予測の前から partial transcript で応答生成を開始。本流(barge-in監視)と投機流(下書き)に fork し、ユーザーが話し続けたら破棄・再計算（RelayS2S 系 dual-path）
3. **レイテンシ隠蔽（相槌/フィラー）** — LLM待ち時間に「うん」「なるほど」「えーっと」を**事前レンダリング済みElevenLabsクリップ**で即時再生し、300-500ms稼ぐ

---

## 自作モジュール詳細

### [2] ターンテイキング層（最重要・最も差別化される部分）
- **状態機械 (FSM)**: LISTEN / THINK / SPEAK / IDLE。制御トークンで「完全な発話 / 偽の割り込み / backchannel」を区別
- **semantic endpointing**: 「話し終えたか」を無音長ではなく意味的完結度で予測
  - LiveKit式（テキスト意味のみで判定する open-weights モデル）と Pipecat式（韻律・音響で判定）の**両signal併用**を検討
  - **日本語固有の難所**: 文末助詞(か/ね/よ/…)、助詞止め・体言止め、フィラー(えーと/あの)、句読点なし発話、ターン末の曖昧さ → 日本語専用にチューニング/データ収集が必要
- **barge-in / backchannel 識別**: AI発話中のユーザー音声を「相槌（継続）」か「割り込み（即停止）」かを学習signalで分類。エネルギー閾値だけだと誤爆する

### [3] 意図理解 + 先読み応答生成
- persona-conditioned LLM（低TTFT重視）。性格・価値観・口調・知識境界・NG事項をシステムプロンプト＋few-shotで注入し、**prompt caching** でpersona/記憶部分の遅延をゼロ化
- speculative generation: partial transcript で投機生成、確定後に整合チェック
- 出力は文/節単位でチャンクし即座にTTSへ

### 人格（性格をもったAI）— 3軸すべてに対応
- **会話人格・一貫した価値観**: 構造化ペルソナ仕様（性格特性 / 価値観 / 口調 / 知識境界 / NG）。長セッションでの人格ドリフト(OOC)を防ぐガードレール。相槌の癖もキャラ単位で定義
- **長期記憶による関係性**: 生ログではなく **semantic summarization**（エピソード記憶＋関係記憶）。セッション終了時に要約 → embedding検索でtop-kを文脈注入。呼び方・過去の話題・感情の機微を「関係状態」として保持し「関係が育つ」感覚を出す
- **自然な間・相槌・割り込み**: 上記[2]のFSM＋相槌/フィラー注入。ペルソナ条件付きで相槌頻度・スタイルを変える

---

## 技術スタック候補（日本語中心）

> いずれも上記**ポート背後のアダプタ**として実装し、検証結果で差し替え可能にする（特定ベンダーに固定しない）。

| 役割 | 現時点の第一候補 | 備考 |
|---|---|---|
| トランスポート/パイプライン骨格 | **Pipecat v1.0**（2026/4 GA）or **LiveKit Agents** | WebRTC/ストリーミング配管は車輪の再発明しない。novel な対話ロジックだけ自作 |
| ターン検出モデル | LiveKit open-weights turn detector（日本語対応の要検証）or 自作チューニング | テキスト意味＋音響韻律の両signal |
| STT (日本語ストリーミング) | Deepgram / AssemblyAI / Google、もしくは kotoba-whisper / ReazonSpeech 系のstreaming | 低遅延partial と精度のトレードオフを実測 |
| LLM（人格推論） | 低TTFT＋prompt caching対応モデル（Claude系等）。終端/相槌判定用に別の小型高速モデル併用も | |
| TTS | **ElevenLabs Flash v2.5**（声優ボイス, sub-500ms streaming） | 既定要件 |
| 記憶 | vector DB（embeddings）＋ 要約ストア | |

> **「ゼロベース構築」の解釈**: novel な対話制御ロジック（[2][3]＋人格・記憶）は完全自作。
> ただし WebRTC/音声I/Oの配管まで自作するのは非効率なので、Pipecat/LiveKit を土台に乗せることを推奨。
> ここは方針判断ポイント（フルスクラッチ vs フレームワーク土台）。

---

## 段階的構築プラン

- **Phase 0 — ポート定義 ＋ PoC cascaded ループ**: 先に各 Port インターフェースと内部標準イベント型を確定し、最小アダプタ（STT/LLM/ElevenLabs）を実装。素朴な無音終端でストリーミング接続し、ベースライン遅延を実測・声優ボイス品質を検証。**ここで抽象化の境界を固めておくのが後段の差し替え運用の前提**
- **Phase 1 — 自然さ（ターンテイキング）★核心**: 日本語 semantic endpointing、barge-in、相槌/フィラーによるレイテンシ隠蔽。体感遅延と割り込み精度を計測
- **Phase 2 — 人格**: ペルソナ仕様、prompt caching、一貫性ガードレール、ペルソナ条件付き相槌
- **Phase 3 — 長期記憶**: セッション要約、検索注入、関係状態の保持
- **Phase 4 — 先読み応答＋仕上げ**: dual-path speculative generation、レイテンシ予算チューニング、評価harness整備

---

## 主要リスク / 日本語固有の難所

- **日本語の終端判定が難しい**: 助詞止め・体言止め・句読点なし・ターン末の曖昧さ → 専用データ/チューニング前提（楽観しない）
- STT の遅延 vs 精度、partial transcript の不安定さ（言い直しで投機生成が無駄打ち）
- 先読み応答の計算浪費・途中で意図が変わった際の誤推測
- 長セッションでの人格ドリフト、記憶検索の関連性（無関係記憶の混入）
- barge-in 誤検知（背景雑音・相槌の誤分類）
- ElevenLabs の遅延ばらつき・スケール時コスト、声優権利の許諾範囲（どの発話まで合成可か）

---

## 検証方法（end-to-end）

1. **レイテンシ計測harness**: 「発話終了 → 応答音声第一波」を段ごとにログ。Phase 0 でベースライン、各Phaseで回帰監視
2. **ターンテイキング評価**: ラベル付き日本語対話セットで「早すぎる割り込み率 / 応答遅延」を測定（τ-Voice 系ベンチの日本語版）
3. **人格一貫性評価**: LLM-judge で長セッション中の OOC（キャラ崩れ）検出
4. **記憶リコール評価**: 事実を注入し、Nセッション後の想起精度を測定
5. **体感自然さ A/B**: 人手評価（cascaded素朴版 vs 自然さ最適化版）

---

## 未確定の方針判断（実装着手前に決めるべき）
- **ポートの粒度確定**（最優先）: 上記 Port 群のIFと内部標準イベント型を Phase 0 で凍結。ここがブレると差し替え自由度が崩れる
- フルスクラッチ vs Pipecat/LiveKit 土台（推奨は後者。`AudioTransport` ポート背後に置くが交換コストは高めなので初期選定は重要）
- ターン検出: テキスト意味モデル / 音響韻律モデル / 両者融合 のどれを主とするか（`TurnDetector` 背後でベンチ比較）
- 個別プロバイダ（STT/LLM/TTS）は固定せず、**ポート背後でベンチして選ぶ**運用に倒す

---

## 設計レビュー反映（DeepSeek-R1 レッドチーム, 2026-06-03）

異種ベンダー（DeepSeek-R1）でのレッドチームレビューを実施。妥当な指摘を以下の設計変更として取り込む（Claude 側の反論・取捨選択込み）。

### 採用（Critical → 設計に反映）
1. **投機応答は「計算のみ」、音声出力は終端確定で必ずゲート**（指摘2・代替案への対応＝最重要）
   - 投機生成（LLMドラフト）は warm-up として走らせてよいが、**スピーカーへ流すのは semantic endpoint 確定後のみ**。確定前の投機音声は絶対に出さない。
   - これで「言い直し（天気→予定）」での誤発声と「STT異常partialの暴走発声」を構造的に封じる。DeepSeek の「投機廃止」案は *投機計算* と *投機発声* を混同しており、廃止すべきは後者のみ。
2. **STT出力の健全性ゲートを `STTProvider` アダプタ契約に追加**（指摘3）
   - 音声区間有無・確信度・異常長・繰り返し検出をアダプタ責務とし、コアには正規化済み＋健全性フラグ付きイベントのみ渡す。
3. **記憶は「追記ログ」ではなく「構造化された関係状態」として設計**（指摘＝1点直すなら）
   - 競合解決ルール（タイムスタンプ＋明示的訂正検出）、忘却ポリシー、スキーマ version 管理、埋め込みモデル差し替え時の再embedding手順を Phase 0/3 で文書化。
   - PII（住所・電話番号等）の検出・マスキングを記憶ポリシーに必須化。
4. **`TTSProvider` アダプタに声優音声ガードレールを内蔵**（指摘4・法的リスク）
   - 許諾範囲を capability として宣言、発話内容フィルタ、**全合成テキストの監査ログ**、アダプタ署名/レビュー。声優人格権侵害リスクの構造的防止。
5. **barge-in 誤検知からの回復遷移を FSM に明記**（指摘3）
   - 誤割り込み判定時：割り込みバッファ保持 → 相槌/雑音と再判定 → 元応答の再開 or 自然な復帰発話。日本語相槌(うん/はい/ええ)と割り込み(いや/ちょっと)の韻律差を学習する教師データ収集を Phase 1 計画に追加。

### 部分採用 / 反論
6. **ポートIF「凍結」は緩める**（指摘5は妥当）: 完全凍結は非現実的。**「破壊的変更を避ける加法的バージョニング＋optionalなcapabilityフィールド」** に再定義。新機能（感情付きpart"ial等）はoptional拡張として足し、コア未対応なら無視できる契約にする。
7. **「体感800ms」の計測定義を厳密化**（指摘5）: 「ユーザー音声停止時刻」→「応答音声の最初の有声フレーム（フィラー含む/含まないを別計測）」と定義し harness に固定。

### 保留（優先度Medium、Phase後送り）
- 複数話者/背景音声の混線（単一話者前提を明記、話者分離は将来課題）
- プロンプトインジェクションによる人格改変（OOC検出ガードレールで Phase 2 対応）
- ネットワーク分断時の FSM 状態復元

---

## 参考（2026年時点の業界動向）
- Speech-to-Speech vs Cascade のアーキ比較（Deepgram / AssemblyAI / Coval）
- Turn Detection / semantic endpointing（LiveKit, AssemblyAI）
- 全二重・barge-in・speculative response（Future AGI 2026, RelayS2S arXiv 2603.23346, τ-Voice benchmark）
- 日本語 TTS / AItuber 構成（Style-Bert-VITS2 / AivisSpeech / ElevenLabs Flash v2.5）
