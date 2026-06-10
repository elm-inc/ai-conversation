# 日本語音声生成 最適化 — 設計 (TTS 日本語特化)

- Status: Draft (v4 — AGPL 回避方針: 本命エンジンを ESPnet2 (Apache-2.0) 主軸 + VOICEVOX (LGPL) 並列に変更)
- Linear Project: [日本語TTS最適化 — セルフホスト (ESPnet/VOICEVOX)](https://linear.app/elm-inc/project/日本語tts最適化-セルフホスト-style-bert-vits2-e3fc1fadcc30)
- Phases: AIC-13 (P-1 ゲート), AIC-8 (P0), AIC-12 (P1), AIC-9 (P2), AIC-14 (P3), AIC-10 (P4), AIC-11 (P5)
- 関連 ADR: [ADR-0001](../adr/0001-cascaded-orchestration-architecture.md)(cascaded + 自作オーケストレーション層), [ADR-0002](../adr/0002-ports-and-adapters.md)(ports & adapters), [ADR-0003](../adr/0003-pipecat-cloud-pipeline.md)(Pipecat Cloud + Daily)
- 最終更新: 2026-06-10
- 方針決定 (本設計の前提):
  - **自然さ優先** — 当面の声は高品質な汎用日本語ボイスでよい。特定声優ボイスのクローンは後段フェーズ。
  - **セルフホスト OSS (GPU) 軸** — アクセント完全制御 + 自前ファインチューニングを取りに行く。
  - **本格パイプライン設計を優先** — 即効策より、レイヤー別の根治設計と移行計画を確定する。

> 「随時更新」の設計方針 (design)。確定した決定の理由 (why) は `docs/adr/` に昇格する。
> このドキュメントは TTS (発話音声生成) の日本語特化のみを扱う。STT / ターンテイキングは別系統 (ai-conversation.md)。

---

## 1. Context — なぜ ElevenLabs では日本語が流暢にならないか

現行は ElevenLabs (`eleven_flash_v2_5` / `eleven_multilingual_v2`, 声優ボイス固定) を
`apps/voice-agent/bot.py` (Pipecat) と `src/aiconv/adapters/tts_elevenlabs.py` (core) で使用している。
日本語の「流暢でない・イントネーション違和感」の症状は、**ElevenLabs 固有の品質問題ではなく構造的な原因**による。

**根本原因: 多言語モデルは日本語のピッチアクセント (高低アクセント) を明示的にモデル化しない。**

- 日本語は **モーラ単位の高低アクセント言語**。同じ音素列でも**アクセント核の位置**で語が変わり (橋/箸/端、雨/飴)、
  アクセント句のまとまり方で自然さが決まる。
- ElevenLabs / Fish Speech 等の多言語 end-to-end モデルは、書記素 (または内部音素) から韻律を**暗黙学習**する。
  日本語データ比率が低く、アクセント核を**入力として受け取れない**ため、
  「外国人が読む日本語」のような平板・不自然な抑揚、固有名詞の読み崩れ、文末イントネーションのズレが出る。
- ElevenLabs の制御手段 (stability / similarity / 発音エイリアス) では**アクセント核を指定できない**。
  → SSML / 発音辞書での小手先改善は天井が低い (§7 で「即効策」として別途整理するが、根治ではない)。

**根治の方向: アクセントを明示入力できる「日本語フロントエンド + アクセント対応音響モデル」へ移行する。**
日本語は規則ベースのアクセント推定 (OpenJTalk 系) が強く、これを音響モデルへ明示的に渡す
VOICEVOX / Style-Bert-VITS2 系が、ローカル日本語 TTS で生き残っている理由がこれ。

---

## 2. 評価軸 (この設計が最適化する対象)

| 軸 | 指標 | 現状 (ElevenLabs) | 目標 |
|---|---|---|---|
| **アクセント正確性** | アクセント核一致率 (主要語・固有名詞) | 低 (制御不能) | 辞書 + 推定で >95% (固有名詞は辞書で 100%) |
| **自然さ** | MOS / judge「自然さ」スコア | 中 | SBV2JE 報告値 MOS≈4.37 (人間 4.38) 水準 |
| **応答遅延 (TTFA)** | 終端確定→最初の音 | ネットワーク往復込み 300-500ms | **150-300ms** (LIVE デュオ要件, §6) |
| **可制御性** | アクセント手動上書き・話速・スタイル | 弱 | アクセント核/話速/感情を API で制御 |
| **声の同一性** | 目標話者との一致 (将来) | 声優クローン (ElevenLabs) | フェーズ後段で fine-tune (§5-L5) |
| **運用** | GPU 占有・スケール | API のみ (運用ゼロ) | セルフホスト GPU (要設計, §6) |

評価ハーネスは既存 `apps/conversation-tester/judge.py` を拡張して回す (§8)。

---

## 3. エンジン選定 (2026-06 時点の現状調査ベース)

セルフホスト・**パーミッシブライセンス (非 AGPL)**・日本語アクセント制御・ファインチューニング可否で評価。
**方針 (v4): AGPL を避ける** (理由は §15)。明示的アクセント制御を保つことが最優先。

| エンジン | ライセンス | 日本語アクセント制御 | 自然さ | Fine-tune (自前声) | 判定 |
|---|---|---|---|---|---|
| **ESPnet2 VITS** | **Apache-2.0** | ◎ `pyopenjtalk_accent_with_pause` で tone 入力 (SBV2 と同方式) | ◎ | ◎ JSUT base→**100発話で適応**可 | **本命 (学習エンジン・本番ホットパス)** |
| **VOICEVOX** | **LGPL-3.0** (core は MIT) | ◎ OpenJTalk + アクセント編集 API | ○ (キャラ声質) | ✕ 任意話者の学習は非公開 | **並列採用** (即時汎用 + アクセント編集 API) |
| Kokoro-82M | Apache-2.0 (重みも) | ○ misaki[ja]=pyopenjtalk 経由 | ◎ | ✕ 学習/クローン非対応 | 対抗 (即時汎用ボイスの選択肢) |
| GPT-SoVITS | MIT | △ 参照音声駆動・明示制御弱 | ○ | ◎ few-shot (1分) | クローン用途の対抗 (アクセント根治は一段下) |
| CosyVoice 2 | Apache-2.0 | △ 暗黙 (LM 型) | ◎ | ◎ | アクセント明示制御弱→根治には不適 |
| ~~Style-Bert-VITS2 / AivisSpeech~~ | ~~AGPL-3.0~~ | ◎ | ◎ | ◎ | **除外 (AGPL, §15)** |

**選定 (v4 — AGPL 回避でエンジンを刷新):**
- **本命 = ESPnet2 VITS (Apache-2.0) を Pipecat ワーカーに in-process ロード**:
  - AGPL を避けたため **in-process が許容** (Apache/MIT)。HTTP/サイドカーを挟まず文/節チャンク逐次合成 + 割り込み
    (送出停止) をプロセス内で制御。レッドチーム §C1 (ストリーミング/割り込み) と §C2 (WAN 回避) を最も素直に満たす。
  - **アクセント根治の核 (pyopenjtalk_accent_with_pause) は ESPnet が標準入力**として受理 = SBV2 と同等の明示制御。
  - **自前声 (P5) の学習が現実的**: JSUT 事前学習 VITS から **100 発話程度で話者適応** (ESPnet2-TTS 論文の実証)。
    声優録音 30分〜数時間で十分。汎用も自前も同一パイプライン。ESPnet-EZ で Python のみ運用も可。
- **VOICEVOX (LGPL) を並列採用**:
  - (a) **即時の汎用ボイス** (既存キャラ・アクセント編集 API) で P0 立ち上げ / A-B baseline。姉妹 `tamayori-tts` (VOICEVOX ONNX) の知見を流用。
  - (b) 任意で **VOICEVOX 互換の配信/アクセント編集 API 層**を自前 ESPnet モデルの前段に流用 (AivisSpeech が SBV2 で採った構成の ESPnet 版)。
  - **VOICEVOX 自体での自前声学習は不可** (VVM 作成は非公開) → 自前声は ESPnet 側に寄せる。LGPL はネットワーク条項なしで SaaS 商用可。
- **汎用ボイス base (P0-P4)**: JSUT 事前学習 (商用は要連絡) か **CC BY-SA jvnv で自前 base** を作る (完全クリーン)。
- §9 Phase 0 で ESPnet (in-process) vs VOICEVOX vs Kokoro を judge で実測し本命を確定する。

---

## 4. パイプライン全体像

```
LLM 出力テキスト (記号/英数字/絵文字/マークダウン混じり)
  │
  ▼  L0 テキスト正規化  ─ 数字/単位/記号/英単語/顔文字 → 読みやすい日本語表記
  │
  ▼  L1 フロントエンド G2P + アクセント推定  ★日本語イントネーションの根治層
  │     pyopenjtalk-plus: MeCab(辞書) → 読み → 音素列 + モーラ + アクセント核 + アクセント句境界
  │     ├─ アクセント辞書 (ユーザー辞書): 固有名詞/キャラ名/専門語の読み+アクセントを固定
  │     ├─ marine (DNN アクセント推定): 規則ベースの誤りを補正
  │     └─ 手動アクセント上書き: AccentPhrase JSON を編集 (固有名詞/重要語)
  │
  ▼  L2 韻律・スタイル制御  ─ 話速/ピッチ/抑揚 (ESPnet 推論パラメータ / X-vector 等)
  │
  ▼  L3 音響モデル  ─ ESPnet2 VITS (Apache-2.0, アクセント=高低/句境界を入力として受理)
  │
  ▼  L4 ボコーダ  ─ VITS E2E 一体 (22.05kHz) → 波形
  │
  ▼  L5 話者同一性  ─ [当面] 商用クリーン汎用モデル(jvnv/VOICEVOX) / [後段] 声優音源で ESPnet 話者適応
  │
  ▼  L6 配信・統合  ─ ストリーミング合成 → AudioFrame 正規化 → ports TTSProvider / Pipecat TTSService
  │
  ▼  L7 評価・回帰  ─ judge 拡張 (アクセント一致率/MOS/A-B/回帰)
```

各層を独立に差し替え・改善できる (ADR-0002 の精神)。**L1 が違和感の主因**であり、ここを入れるだけで
エンジンが同じでも日本語の自然さは大きく改善する。

---

## 5. レイヤー別 設計

### L0. テキスト正規化 (Text normalization)

LLM 出力は記号・英数字・絵文字・マークダウンを含み、そのまま読ませると崩れる。
- 数字/単位/通貨/日付/時刻/序数 → 読み下し (例: `3,000円` → 「さんぜんえん」、`14:30` → 「じゅうよじさんじゅっぷん」)。
- 英単語/略語 → カタカナ読み or アルファベット読み (辞書で制御。`AI`→「エーアイ」, `OpenAI`→「オープンエーアイ」)。
- 記号/絵文字/マークダウン記法の除去・読み替え。
- **エッジケース (レッドチーム §2.1)**:
  - コードブロック/インラインコード (` ``` `) → 「バッククォート」等と読まない。除去 or 「(コード)」要約。
  - 数式・記号列 (`E=mc^2`, `->`, `|`) → 読み規則を定義 (LLM 側プロンプトで音声前提の出力にするのが本筋)。
  - 絵文字・顔文字の連打 → 除去 (読み上げない)。
  - **英略語のピリオドが文境界に誤認識** (`A.1` `Q.2` `Mr.`) → L0 で保護し、`_sentences()` の文分割が短文連発で崩れるのを防ぐ。
  - 数字の文脈依存読み (`3-4` = 「さんたいよん/さんからよん」, `2025` = 年/数) → 文脈ヒューリスティック + 残差は辞書。
- **責務分界**: ここは「読み崩れ」対策。アクセントは L1 の担当。
- **音声前提の出力**: 根本的にはチャットボットの LLM プロンプトを「音声読み上げ前提」にし、記号/コードを出させない
  (bot.py のペルソナに既に一部あり) のが最も堅牢。L0 はその取りこぼしの安全網。
- 実装: ルールベース正規化 + 既知語辞書。難しい正規化 (口語化) は LLM 側プロンプトでも一部吸収。

### L1. フロントエンド (G2P + アクセント推定) ★最重要

**`pyopenjtalk-plus` (tsukumijima fork, AivisSpeech も採用) を採用。**
書記素 → MeCab 形態素解析 → 読み → 音素列 + モーラ + **アクセント核位置** + アクセント句境界 を生成する。

3 つの精度レバー:
1. **アクセント辞書 (ユーザー辞書)** — 固有名詞・キャラ名・サービス名・専門語を CSV で登録。
   `mecab_dict_index()` でコンパイル → `update_global_jtalk_with_user_dict()` で適用。
   読み + モーラ数 + アクセント核位置を指定でき、**固有名詞のアクセントを 100% 固定**できる。
   → プロジェクトの語彙 (キャラ名「あい」「ゆう」, 作品名, テーマ語彙) を辞書化。
   テーマ前提知識注入 (`_expand_theme` の keyterms, bot.py) と**同じ語彙源**から辞書を自動生成できる。
2. **marine (DNN アクセント推定)** — pyopenjtalk `run_marine=True` で規則ベースの推定誤りを統計補正。
   一般語の複合語アクセント等で効く。辞書 (固有名詞) と併用 (辞書が優先)。
3. **手動アクセント上書き** — AccentPhrase 構造 (pyopenjtalk 由来 / VOICEVOX `audio_query` JSON 互換) を編集して、
   個別語のアクセント核・句境界を強制。誤りが残る重要語のフォールバック。

辞書運用: `data/accent_dict/*.csv` を単一ソースにし、ビルド時にコンパイル。
固有名詞の追加は PR で管理 (誰でも追記でき、回帰テストで読みを検証, §8)。

**辞書未登録語のフォールバック (レッドチーム §2.2):** 辞書 100% 固定は「登録済み語のみ」。未登録の固有名詞は
marine (DNN 推定) → 規則ベースの順でフォールバックし、誤読しても**破綻はしない**設計とする。
運用で漏れを潰すため **辞書ヒット率を観測** (§13) し、テーマ keyterms (`_expand_theme`) に出た語のうち未登録のものを
警告ログに出して辞書化を促す。**内部脅威対策**: 辞書 CSV の読み変更は「期待読み」を人間レビュー必須にする (§12)。

### L2. 韻律・スタイル制御 (Prosody / style)

- **話速 / ピッチ / 抑揚スケール** — VOICEVOX 採用時は `audio_query` パラメータ (speedScale/pitchScale/
  intonationScale 等)、ESPnet 採用時は推論時の話速制御で、発話のテンポと抑揚の強さを調整。
  デュオの「間」やキャラ差 (あい=落ち着き / ゆう=快活) をここで付ける。
- **話者/スタイル** — ESPnet 多話者 VITS の話者埋め込み (X-vector / spk-id) で話者・トーンを切替。
  感情スタイルが要る場合は感情ラベル付きデータ (jvnv 等) で学習。
- **キャラ別プリセット** — あい / ゆう のスタイル + 話速 + ピッチを preset 化 (presets.py 思想を踏襲)。

### L3. 音響モデル (Acoustic model)

- **ESPnet2 VITS (Apache-2.0)**。音素 + **アクセント (高低/句境界) を入力**として受理するため、
  L1 のアクセント情報がそのまま反映される (= 日本語イントネーション根治の本体)。G2P は `pyopenjtalk_accent_with_pause`。
- VITS は E2E (音響+ボコーダ一体, 22.05kHz)。代替に Matcha-TTS (flow matching, 高速) / FastSpeech2 も ESPnet にある。
- **学習**: JSUT 事前学習 VITS から `--init_param` で話者適応 (100 発話〜)。汎用 base は jvnv (CC BY-SA) で自前作成可。
- (旧 v3 の Style-Bert-VITS2 JP-Extra は AGPL のため除外, §15)。

### L4. ボコーダ (Vocoder)

- ESPnet VITS は E2E (音響+ボコーダ一体) で 22.05kHz 出力。→ core の AudioFrame は
  16kHz PCM に正規化 (現行 `_PCM_16K` 規約に合わせる, L6)。リサンプリングはアダプタ層で吸収。

### L5. 話者同一性 / 学習 (Voice identity / fine-tuning)

- **当面 (自然さ優先フェーズ)**: 商用クリーンな汎用日本語モデル (CC BY-SA jvnv 自前 base / VOICEVOX キャラ / Kokoro) から
  あい / ゆう に合う声質を選定。クローン不要で即立ち上げ。
- **後段 (声優ボイス再現フェーズ, ADR-0001 の本要件)**:
  権利クリアな声優音源 (台本ペア 30分〜数時間、最小 100 発話〜) で **ESPnet VITS を話者適応 (`--init_param`)**。
  音素・アクセントの手アノテーション不要 (台本の「読み」さえ正しければ `pyopenjtalk_accent_with_pause` が自動付与)。
  学習済みは ESPnet 形式のまま in-process ロード (任意で ONNX 化)。base が permissive なら派生も非 AGPL。
- **許諾ガードは維持**: 既存 `VoiceLicense` / `AuditSink` (ports.py, tts_elevenlabs.py) の思想を
  新アダプタにも実装し、声優人格権を構造的に保護する。

### L6. 配信・統合 (Serving / integration)

2 つの統合先がある (ADR-0003 の二系統):

1. **core / PoC・コンテンツ録音** (`src/aiconv/adapters/`, `apps/conversation-tester/record_text.py`)
   - 新アダプタ `tts_espnet.py` (+ 並列で `tts_voicevox.py`) を `TTSProvider` (ports.py) として実装。
     `synthesize(text_chunks) -> AudioFrame` / `interrupt()` のIF は不変、core は無改修で差し替わる。
   - `tts_elevenlabs.py` と同じく `VoiceLicense` / `AuditSink` / `_PCM_16K` 正規化を踏襲。
2. **本番 / LIVE デュオ** (`apps/voice-agent/bot.py`, Pipecat ワーカー)
   - Pipecat の `TTSService` を継承し、**ESPnet2 VITS を in-process ロード**して合成する
     (AGPL を避けたため in-process 可, §3/§15)。文/節チャンク逐次 + 割り込み (送出停止) をプロセス内で実装。
     `ElevenLabsTTSService` を差し替える。VOICEVOX 採用時は HTTP/同梱 core 経由。
   - ワーカーは **GPU と同居** (§11)。in-process なので IPC・WAN は不要。
   - LIVE デュオは現状 ElevenLabs ボイス ID 2 種 (あい/ゆう)。新エンジンでも 2 話者モデル (jvnv 等) を用意。
**デプロイ・トポロジと可用性は §11 で独立に扱う** (レッドチーム最重要指摘: WAN ホップ・GPU 相乗り・フォールバック)。

### L7. 評価・回帰 (Eval)

§8 参照。

---

## 6. レイテンシ予算 (LIVE デュオ / ライブエージェント要件)

目標 (ai-conversation.md): 発話終了 → 応答音声開始 体感 < 800ms。TTS の TTFA 配分は **150-300ms**。

- VITS 系は RTF ≪ 1 で、**文/節チャンク単位のストリーミング合成**で最初の音を早く出せる。
  既存 orchestrator は `_sentences()` で文境界チャンク化済み → そのまま活かす。
- **【訂正・レッドチーム §C2】セルフホスト = 低遅延、は TTS とワーカーが同居している場合のみ成立**。
  本番が Pipecat Cloud (クラウド) で TTS が自宅/オフィス GPU だと、**毎合成で WAN 往復**が乗り逆効果。
  → TTS は Pipecat ワーカーと**同一ホスト/同一データセンタに同居**させるのが前提 (§11)。in-process なら往復ゼロ。
- フロントエンド (L0/L1) の処理は数 ms オーダーで TTFA にほぼ影響しない。
- **リスク (レッドチーム §C3): vLLM(Qwen) と GPU 相乗りすると、vLLM のバッチ推論が TTS を待たせ TTFA 超過**。
  → リアルタイム TTS は vLLM と**同一 GPU に相乗りさせない** (§11 で専用 GPU/インスタンスを既定とする)。
- フィラー (bot.py FILLER) は引き続きレイテンシ隠蔽に使える。フィラー音声も新エンジンで事前/逐次生成。
  WAN フォールバック時 (§11) の数百 ms もフィラーで吸収する。

---

## 7. 即効策 (本設計の対象外だが記録 — ElevenLabs を当面併用する場合)

本命はエンジン移行だが、移行完了まで現行 ElevenLabs を使い続ける場合の暫定策 (天井は低い):
- **発音辞書 / エイリアス**: 固有名詞の読み崩れを文字列置換で部分的に矯正 (アクセントは直せない)。
- **カタカナ表記化**: LLM 出力段で英単語・記号をカタカナへ (L0 の一部を前倒し)。
- これらは L0/L1 の知見を ElevenLabs に部分適用するもので、移行後は L0 として正式化される。
→ 方針 (本格パイプライン優先) によりこのフェーズは**最小限**に留め、Phase 0 から本命に着手する。

---

## 8. 評価・回帰ハーネス

既存 `apps/conversation-tester/judge.py` (ルーブリック採点) を拡張:
- **アクセント一致率**: 代表文セット (固有名詞・最小対立ペア「橋/箸」等・テーマ語彙) について、
  L1 出力アクセント核 vs 正解辞書 を機械照合。固有名詞は辞書登録で 100% を担保。
- **読み回帰テスト**: `data/accent_dict` 追加時に「期待される読み/アクセント」をテスト化
  (`tests/test_tts_frontend.py` 新設)。辞書 PR で読み崩れを CI で検出。
- **自然さ A/B**: ElevenLabs 現行 vs 新エンジンを judge「自然さ」「整合性」で比較 (judge `--repeat N` で分散実測)。
- **遅延実測**: 既存 `[probe]` ログ (bot.py TurnProbe) で TTFA を本番経路で計測。
- **MOS (任意)**: 人手 or 自動 MOS 予測でスポット評価。

---

## 9. 段階移行計画 (Phase)

| Phase | 目的 | 主な成果物 | 完了条件 |
|---|---|---|---|
| **P-1 ゲート** | 着手前の前提検証 (レッドチーム §4.3/§C4) | ライセンス精査済 (§15): **AGPL 回避でクリア**。残は軽微 (帰属表示・JSUT連絡・naist-jdic確認) + トポロジ A/B 確定 | 採用スタックが商用クリーンと確認済 & TTS 同居先 (案A/B) 確定 → **ほぼ完了** |
| **P0 検証/PoC** | エンジン実機比較・遅延/VRAM 実測 | **ESPnet (in-process) / VOICEVOX / Kokoro** で代表文合成し TTFA/RTF/VRAM 実測 (vLLM 相乗り有無も)。ElevenLabs と A/B | judge で新エンジンが自然さ優位 & **TTFA<300ms を同居構成で実測**、本命エンジン & GPU 配置確定 |
| **P1 フロントエンド** | L0/L1 を実装し**アクセント根治** | テキスト正規化 + pyopenjtalk-plus + ユーザー辞書 + marine。`data/accent_dict` | 代表文のアクセント一致率 >95%、固有名詞 100%、回帰テスト緑 |
| **P2 エンジン差し替え (core)** | `TTSProvider` 新アダプタ | `tts_espnet.py` (+`tts_voicevox.py`) + VoiceLicense/AuditSink/16k 正規化。record_text / PoC で動作 | core 無改修で差し替え、コンテンツ録音が新エンジンで生成可 |
| **P3 本番統合 (Pipecat)** | LIVE デュオ/ライブで実音声 | Pipecat `TTSService` (in-process ESPnet)、あい/ゆう 2 話者、ストリーミング+interrupt、**ElevenLabs フォールバック** (§11)、観測性 (§13) | LIVE デュオが新エンジンで安定動作、TTFA 目標達成、障害時フォールバック動作 |
| **P4 スタイル/話者調整** | L2 + キャラ別プリセット | 話速/ピッチ/スタイル preset (あい/ゆう) | judge 人格一貫性スコア維持/向上 |
| **P5 (後段) 声優 fine-tune** | ADR-0001 の声優ボイス再現 | 声優音源で **ESPnet VITS 話者適応** (100発話〜)、許諾ガード | 目標話者一致 & 許諾監査ログ動作 |

**P-1 ゲートは AGPL 回避で実質クリア** (§15。残は帰属表示等の軽微 + トポロジ A/B)。
P0→P1 を最優先 (L1 だけで体感が大きく変わる)。P5 は別フェーズ (声の同一性要件が再優先化した時)。

---

## 10. 未確定の方針判断 (Open questions)

1. **トポロジ確定 (§11)** — Pipecat ワーカーと TTS-GPU をどこで同居させるか (案 A クラウド GPU / 案 B 自前GPU+ワーカー移設)。残る主要論点。
2. **本命エンジン確定** — ESPnet (本命) を P0 の judge 実測で確定。VOICEVOX/Kokoro を汎用ボイスでどこまで併用するか。
3. **汎用 base の選択** — JSUT 事前学習 (要連絡) か CC BY-SA jvnv 自前 base か (§15)。
4. **辞書の単一ソース** — `data/accent_dict` をテーマ keyterms (`_expand_theme`) と統合し自動生成するか。
5. **声優音源の調達** — P5 着手時の録音台本・収録量 (100発話〜)・許諾範囲 (`VoiceLicense.allow` の定義)。

> **解決済**: ライセンス精査 (§15, AGPL 回避で商用クリア) / エンジン選定 (§3, SBV2→ESPnet+VOICEVOX) /
> in-process 可否 (AGPL 回避で in-process 復活、サイドカー不要)。

---

## 11. デプロイ・トポロジと可用性 ★レッドチーム最重要

**問題 (レッドチーム §C2/§3.1):** 本番計算が Pipecat Cloud (クラウド)、TTS-GPU が自前 (自宅/オフィス) だと、
(a) 毎合成で WAN 往復が乗り TTFA<300ms が崩れ、(b) 自前回線の分断で**ライブが無音化**する。
ElevenLabs は外部 API なのでこの問題が無かったが、セルフホストは TTS と計算の同居が前提条件になる。

**取りうるトポロジ (P-1 で決定):**
| 案 | 構成 | Pros | Cons |
|---|---|---|---|
| A. クラウド GPU 同居 | Pipecat ワーカー + ESPnet を GPU 付きクラウド (同一 region) に同居 | WAN ゼロ・スケール容易・ADR-0003 の思想維持 | GPU クラウドコスト |
| B. 自前 GPU + ワーカー移設 | Pipecat ワーカー自体を自前 GPU ホストで動かす (Pipecat Cloud から自前ホストへ) | 既存 GPU 資産活用・WAN ゼロ | 自前ホスト運用・公開/接続経路・ADR-0003 から逸脱 |
| C. リモート TTS (非推奨) | ワーカーは Pipecat Cloud、TTS だけ自前 GPU を WAN 越しに叩く | 既存構成の最小変更 | WAN 往復・可用性が自前回線依存 (レッドチームが否定) |

**推奨: A (本番) / 検証は B も可**。いずれも **TTS は vLLM(Qwen) と別 GPU/インスタンス** (§C3)。

**プロセス構成 (v4: AGPL 回避で簡素化):** ESPnet は Apache-2.0 なので **ワーカーに in-process ロードしてよい**
(v3 の AGPL サイドカー隔離は不要になった)。同一ホストで GPU 直結 = WAN ゼロ・IPC オーバーヘッドなし。
案 C (WAN 越し) はレイテンシ面で依然不可 → **案 A/B のみ**。
(GPU プロセス管理上、TTS を別プロセスに分けたい場合は任意で可。ただし license 上の要請ではない。)

**可用性 (フォールバック):** TTS 合成のタイムアウト (例 1.5s) で **ElevenLabs へ自動フォールバック**し、
無音を出さない。フォールバック中もフィラーで間を持たせる。未完了ジョブのリソースリーク防止に
合成にデッドラインを設定し、割り込み時は確実にキャンセルする。リトライは**べき等キー** (text+voice+params ハッシュ)
で重複合成を防ぐ。

## 12. セキュリティ・内部脅威 (レッドチーム §4)

- **TTS エンドポイントの認証 (§4.1)**: VOICEVOX を別プロセス HTTP で使う場合や TTS を別プロセス化する場合は
  **認証なしで公開しない**。**localhost (unix socket / ループバック) 限定**にすれば攻撃面は最小。
  in-process ESPnet なら TTS の攻撃面はそもそも無い。外部公開時のみ Tailscale ACL / mTLS。
- **声優人格権の構造保護 (§4.1, P5)**: `VoiceLicense` で許諾外テキストを合成拒否し `AuditSink` に全合成を記録。
  クローン実装後はレート制限と入力フィルタも併用。
- **辞書の内部脅威 (§4.2)**: `data/accent_dict` の読み変更は CODEOWNERS レビュー必須。回帰テストは
  「期待読みの正しさ」までは保証しないため、**人間レビューを手続きとして固定**。
- **サプライチェーン (§4.3)**: モデル/コーパスのライセンスは P-1 で精査。weights のハッシュ固定・取得元の検証。

## 13. 観測性 (レッドチーム §5.3)

最低限のメトリクスを P3 までに入れる (これが無いと P0 実測以降の劣化を検出できない):
- TTS 合成レイテンシ TTFA / 全体 (P50/P95/P99)、RTF
- アクセント**辞書ヒット率** (未登録固有名詞の検出 → 辞書化トリガ)
- GPU 使用率・VRAM、同時セッション数
- フォールバック発火率・WAN 往復 (案 B/C 時)
- 既存 `[probe]` ログ (bot.py TurnProbe) と統合し本番経路の実 TTFA を可視化。

## 14. デプロイ・ロールバック (レッドチーム §5.2)

- **辞書コンパイル**: `data/accent_dict` → ビルド時にコンパイルしイメージに焼く (実行時生成しない)。
- **モデル配布**: ESPnet weights (+ VOICEVOX vvm) はイメージ同梱 or 起動時取得 (ハッシュ検証)。前者推奨。
- **ロールバック**: TTS は `TTSService` 実装/環境変数で ElevenLabs ↔ ESPnet ↔ VOICEVOX を切替可能にする (即時ロールバック)。
  **辞書変更はデータなので別管理** (辞書の git revert で戻す)。
- **帰属表示**: jvnv (CC BY-SA) / VOICEVOX キャラ規約のクレジットをアプリ内に掲出 (§15)。

---

## 15. ライセンス (P-1 精査結果 — 2026-06-11)

**方針: AGPL を避ける** (ユーザー決定)。商用利用可否は 3 層で別々に効く。**v4 採用スタックは全層パーミッシブ/非ネットワークコピーレフト**:

| 層 | コンポーネント | ライセンス | 商用 | 備考 |
|---|---|---|---|---|
| フロントエンド L0/L1 | pyopenjtalk-plus | MIT | ✅ | |
| | OpenJTalk / HTS Engine | Modified BSD / BSD | ✅ | |
| | marine (任意, DNNアクセント) | Apache-2.0 | ✅ | |
| | naist-jdic 等辞書 | 概ね BSD 系 (要一次確認) | ⚠️ | |
| **音響エンジン (本命)** | **ESPnet2 (VITS/Matcha)** | **Apache-2.0** | ✅ | **in-process 可** (コピーレフト無) |
| エンジン (並列) | VOICEVOX ENGINE / core | LGPL-3.0 / MIT | ✅ | **ネットワーク条項なし→SaaS 可**。キャラ別規約あり |
| 対抗 | Kokoro-82M | Apache-2.0 (重みも) | ✅ | 即時汎用ボイス |
| | GPT-SoVITS | MIT | ✅ | クローン用途 |
| モデル base/汎用ボイス | JSUT 事前学習 | JSUT 規約 (商用歓迎・要連絡) | ✅ | 連絡で商用可 |
| | jvnv コーパス (自前 base 用) | CC BY-SA 4.0 | ✅ | 帰属+継承。**ネットワーク条項なし** |
| ~~除外~~ | ~~Style-Bert-VITS2 / AivisSpeech / base 重み~~ | ~~AGPL-3.0~~ | ❌ | **AGPL のため不採用** |
| ~~除外~~ | ~~AivisSpeech「Anneli」~~ | 声優許諾問題 | ❌ | 不使用 |

**結論:**
1. **全採用スタックが商用クリーン**。ESPnet=Apache-2.0、VOICEVOX=LGPL(ネット条項なし)、フロントエンド=MIT/BSD/Apache。
   **AGPL のネットワーク・コピーレフトを構造的に回避** → 本番ワーカーへ in-process ロードしても開示義務なし。
2. **モデル重みの encumbrance も解消**: AGPL base を捨て、**JSUT 事前学習 (要連絡) or CC BY-SA jvnv 自前 base** を使う。
   CC BY-SA はネットワーク条項がなく、SaaS では重みを配布しないため実務的義務は**帰属表示**のみ。
3. **自前声 (P5) も AGPL を継承しない** — ESPnet (Apache) で permissive base から適応学習する。
4. **生成音声は元々コピーレフト非カバー** (プログラム出力)。

**残タスク (軽微・非ブロッキングに格下げ):**
- naist-jdic 等辞書の商用条項を一次確認 (概ね BSD 系で問題ない見込み)。
- CC BY-SA / VOICEVOX キャラ規約の**帰属表示の掲出箇所** (アプリ内クレジット) を決定。
- JSUT 事前学習 base を商用利用する場合は作者へ連絡 (or jvnv 自前 base で回避)。

> **P-1 ゲートの最大ブロッカーだった AGPL 問題は「AGPL を採用しない」で解消。** 法務の重い確認は不要になり、
> ゲートは事実上クリア (残は軽微な帰属・連絡手続き)。トポロジは §11 (GPU 同居) のみ要確定。

---

## 16. 関連

- 上流: [ai-conversation.md](ai-conversation.md) (全体設計, レイテンシ予算, ターンテイキング)
- 統合先コード: `src/aiconv/adapters/tts_elevenlabs.py` (差し替え元), `src/aiconv/core/ports.py` (`TTSProvider`),
  `apps/voice-agent/bot.py` (Pipecat TTS), `apps/conversation-tester/{record_text.py,judge.py,presets.py}`
- 採択時に ADR 化する論点: 「日本語 TTS は OpenJTalk 系フロントエンド + アクセント対応音響モデルを
  セルフホストする」(ADR-0003 の TTS=ElevenLabs を部分 supersede)。
