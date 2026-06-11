# 日本語音声生成 最適化 — 設計 (TTS 日本語特化)

- Status: Draft (v6 — セルフホストエンジンを AivisSpeech (Style-Bert-VITS2/ONNX, LGPL) に一本化)
- Linear Project: [日本語TTS最適化 — セルフホスト (AivisSpeech/SBV2)](https://linear.app/elm-inc/project/日本語tts最適化-セルフホスト-style-bert-vits2-e3fc1fadcc30)
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

セルフホスト・ライセンス・日本語アクセント制御・自然さ・ファインチューニング可否で評価。
**確定: P0 実機実測 (31文・客観 f0 + 実聴) で本命 = AivisSpeech。セルフホストは AivisSpeech に一本化** —
VOICEVOX/ESPnet/Kokoro は評価済み・不採用、ElevenLabs は障害時フォールバックのみ。経緯は下記。

| エンジン | ライセンス | 疑問/口語イントネーション (実測 f0) | 自然さ (実聴) | アクセント制御 | 自前声学習(P5) | 判定 |
|---|---|---|---|---|---|---|
| **AivisSpeech** (Style-Bert-VITS2 / ONNX) | **エンジン=LGPL-3.0** (ONNX推論・AGPL推論lib非依存) | ◎ 疑問 f0 最強上昇 (全3問↑。VOICEVOXが外す pn-yonezu もクリア) | ◎ **圧倒的に安定** | ◎ VOICEVOX互換編集 | ◎ SBV2 fine-tune→AIVM | **本命 (本番)** |
| VOICEVOX | LGPL-3.0 (core MIT) | ○ 概ね上昇 (一部外す) | ○ 安定 | ◎ OpenJTalk編集 | ✕ 自前学習不可 | 不採用 (同プロトコルだがエンジンは AivisSpeech に一本化) |
| ~~ESPnet2 VITS (jsut)~~ | Apache-2.0 | ✕ **疑問が下降** (accent も prosody も f0↓) | △ 平叙が方言的 | ◎ | ◎ 但し会話コーパス要 | **不採用** (off-the-shelf 音質不足) |
| ElevenLabs | 商用API | ○ 上昇するが発音崩れ (「ききてる」) | △ | ✕ 制御不可 | ○ クローン | 基準・障害時フォールバック |
| Kokoro / GPT-SoVITS | Apache / MIT | 未測 | 未測 | △ | ✕ / ◎ | 対抗 (未評価) |

**選定 (v5 — 実機実測で確定):**
- **本命 = AivisSpeech** (Style-Bert-VITS2 を ONNX で推論する LGPL エンジン)。
  - **構成**: AivisSpeech Engine を**外部 HTTP プロセス** (`:10101`, **VOICEVOX 互換 API**) で起動し、正典アダプタ `tts_aivis` (`AivisSpeechTTS`) を向ける。
    本番ワーカーは HTTP 越し = **プロセス分離**でクリーン (in-process 取り込みなし → コピーレフト非伝播)。
  - **実測根拠**: 疑問 f0 末尾傾き (ch-ai +845 / ch-yuu +406 / pn-yonezu +706 Hz/s) で全エンジン中最強の上昇。
    ESPnet-jsut は accent/prosody とも f0 下降 (疑問にならない)・平叙が方言的で**不採用**。VOICEVOX は自然だが自前声学習不可。
    AivisSpeech は SBV2 の自然プロソディで**疑問も口語も自然**かつ**自前声学習可**かつ**エンジン LGPL**。
  - **既定モデルは コハク/まお** (Anneli ではない)。自前声は SBV2 fine-tune → AIVM 変換 (P5)。
- **一本化**: セルフホストエンジンは **AivisSpeech 単一**。VOICEVOX は同一ワイヤプロトコル (audio_query/synthesis)
  なので同アダプタ `tts_aivis` で叩けるが、エンジンとしては不採用 (自前声学習不可)。ESPnet/Kokoro も評価済み・不採用。
  **ElevenLabs は障害時フォールバックのみ** (P3 の `TTS_ENGINE` ゲートで AivisSpeech↔ElevenLabs)。
- **ESPnet は不採用** (off-the-shelf 音質)。コードは評価記録として残置 (`tts_espnet` / bench)。
- **遅延 (実測)**: AivisSpeech **GPU で TTFA 102-124ms** (CPU 285-545ms)、目標 <300ms クリア。
- **残**: ① バンドルモデル(コハク/まお)の商用ライセンス確認、② 自前声学習の base 選択 (§15)、③ GPU 同居トポロジ A/B 確定 (§11)。

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
  ▼  L2 韻律・スタイル制御  ─ 話速/ピッチ/抑揚 (audio_query パラメータ) + SBV2 スタイル/感情
  │
  ▼  L3 音響モデル  ─ Style-Bert-VITS2 (AivisSpeech engine, ONNX。アクセント=audio_query で編集可)
  │
  ▼  L4 ボコーダ  ─ SBV2 内蔵 (HiFi-GAN 系) → 波形
  │
  ▼  L5 話者同一性  ─ [当面] AivisHub の商用可モデル / [後段] 声優音源で SBV2 fine-tune→AIVM
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
  intonationScale 等) で発話のテンポと抑揚の強さを調整 (AivisSpeech は VOICEVOX 互換)。
  デュオの「間」やキャラ差 (あい=落ち着き / ゆう=快活) をここで付ける。
- **話者/スタイル** — SBV2 はモデルごとに複数スタイル (ノーマル/あまあま/せつなめ 等) を持ち、style id で切替。
  感情スタイルが要る場合は感情ラベル付きデータで学習 (SBV2 のスタイル埋め込み)。
- **キャラ別プリセット** — あい / ゆう のスタイル + 話速 + ピッチを preset 化 (presets.py 思想を踏襲)。

### L3. 音響モデル (Acoustic model)

- **Style-Bert-VITS2 (AivisSpeech engine で ONNX 推論)**。BERT 由来の文脈プロソディで**疑問・口語が自然**
  (P0 実測で全エンジン中最良)。アクセントは VOICEVOX 互換 `audio_query` の AccentPhrase で編集可能。
- AivisSpeech Engine を**外部 HTTP プロセス** (`:10101`) で動かし、ワーカーから VOICEVOX 互換 API で叩く。
- **学習**: 声優音源で SBV2 を fine-tune → AIVM/AIVMX に変換して AivisSpeech へ載せる (L5/P5)。
- (旧 v4 の ESPnet2 VITS は P0 実測で off-the-shelf 音質不足 → 不採用, §3)。
- (旧 v3 の Style-Bert-VITS2 JP-Extra は AGPL のため除外, §15)。

### L4. ボコーダ (Vocoder)

- SBV2 内蔵ボコーダ (HiFi-GAN 系)。AivisSpeech が wav を返す → core の AudioFrame は
  16kHz PCM に正規化 (現行 `_PCM_16K` 規約に合わせる, L6)。リサンプリングはアダプタ層で吸収。

### L5. 話者同一性 / 学習 (Voice identity / fine-tuning)

- **当面 (自然さ優先フェーズ)**: AivisHub の商用可 AIVM モデル (コハク/まお 等、各規約確認) や VOICEVOX キャラから
  あい / ゆう に合う声質を選定。クローン不要で即立ち上げ。
- **後段 (声優ボイス再現フェーズ, ADR-0001 の本要件)**:
  権利クリアな声優音源 (台本ペア 30分〜数時間) で **Style-Bert-VITS2 を fine-tune → AIVM/AIVMX に変換**して
  AivisSpeech に載せる。SBV2 学習をローカルで回す行為は private use で AGPL 非発動 (§15)。
  **base 重みのライセンス**だけ注意: permissive/scratch base ならクリーン、litagin の AGPL base からの fine-tune は重みが AGPL グレー。
- **許諾ガードは維持**: 既存 `VoiceLicense` / `AuditSink` (ports.py, tts_elevenlabs.py) の思想を
  新アダプタにも実装し、声優人格権を構造的に保護する。

### L6. 配信・統合 (Serving / integration)

2 つの統合先がある (ADR-0003 の二系統):

1. **core / PoC・コンテンツ録音** (`src/aiconv/adapters/`, `apps/conversation-tester/record_text.py`)
   - 既存 `tts_aivis.py` (`TTSProvider`) の URL を **AivisSpeech (`:10101`) に向ける**だけで動作 (P2 実装済・実機確認済)。
     `synthesize(text_chunks) -> AudioFrame` / `interrupt()` のIF は不変、core は無改修。
   - `VoiceLicense` / `AuditSink` / `_PCM_16K` 正規化を踏襲。
2. **本番 / LIVE デュオ** (`apps/voice-agent/bot.py`, Pipecat ワーカー) — P3 (AIC-14)
   - Pipecat の `TTSService` を継承した **`AivisTTSService`** が、**外部 HTTP の AivisSpeech Engine** を
     VOICEVOX 互換 API (`audio_query`→`synthesis`) で叩く。L0 normalize 後に合成、音声フレームを yield、割り込み対応。
     `ElevenLabsTTSService` を `TTS_ENGINE=aivis` で差し替え (既定は ElevenLabs=本番無影響)。**ElevenLabs フォールバック**付き。
   - AivisSpeech は LGPL の**外部プロセス**なので worker に copyleft 非伝播 (§15)。GPU は AivisSpeech 側。
   - LIVE デュオは あい/ゆう = 2 つの style id (`AIVIS_SPEAKER_AI` / `AIVIS_SPEAKER_YUU`)。
**デプロイ・トポロジと可用性は §11 で独立に扱う** (WAN ホップ・GPU・フォールバック)。

### L7. 評価・回帰 (Eval)

§8 参照。

---

## 6. レイテンシ予算 (LIVE デュオ / ライブエージェント要件)

目標 (ai-conversation.md): 発話終了 → 応答音声開始 体感 < 800ms。TTS の TTFA 配分は **150-300ms**。

- VITS 系は RTF ≪ 1 で、**文/節チャンク単位のストリーミング合成**で最初の音を早く出せる。
  既存 orchestrator は `_sentences()` で文境界チャンク化済み → そのまま活かす。
- **【訂正・レッドチーム §C2】セルフホスト = 低遅延、は TTS とワーカーが同居している場合のみ成立**。
  本番が Pipecat Cloud (クラウド) で TTS が自宅/オフィス GPU だと、**毎合成で WAN 往復**が乗り逆効果。
  → AivisSpeech Engine を Pipecat ワーカーと**同一ホスト/同一データセンタに同居**させるのが前提 (§11)。localhost HTTP なら往復は無視できる。
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
| **P-1 ゲート** | 前提検証 | ライセンス: **AGPL は「LGPL エンジンを外部プロセスで使う」で解消** (§15)。残: AivisHub モデル規約確認・トポロジ A/B | ✅ **ほぼ完了** |
| **P0 検証/PoC** ✅ | エンジン実機比較 | AivisSpeech/VOICEVOX/ESPnet/ElevenLabs を実測。**本命=AivisSpeech** (疑問 f0 最強・実聴最良)。ESPnet 不採用 | **Done** (AIC-8) |
| **P1 フロントエンド** ✅ | L0/L1 **アクセント根治** | テキスト正規化 + pyopenjtalk-plus + ユーザー辞書 + marine。`data/accent_dict` | **Done** (AIC-12, 読み18/18) |
| **P2 エンジン差し替え (core)** ✅ | `TTSProvider` アダプタ | `tts_aivis.py` を **AivisSpeech (`:10101`) に向けて動作** (実機確認済)。VoiceLicense/AuditSink/16k | **Done** (AIC-9) |
| **P3 本番統合 (Pipecat)** | LIVE デュオ/ライブで実音声 | **`AivisTTSService`** (外部HTTP) + **ElevenLabs フォールバック** + `TTS_ENGINE=aivis` ゲート、あい/ゆう 2 style id、観測性 (§13) | LIVE デュオが AivisSpeech で安定、TTFA 達成、フォールバック動作 |
| **P4 スタイル/話者調整** | L2 + キャラ別プリセット | 話速/ピッチ + SBV2 スタイル preset (あい/ゆう) | judge 人格一貫性スコア維持/向上 |
| **P5 (後段) 声優 fine-tune** | ADR-0001 の声優ボイス再現 | 声優音源で **SBV2 fine-tune→AIVM**、base ライセンス選択 (§15)、許諾ガード | 目標話者一致 & 許諾監査ログ動作 |

**P-1〜P2 は完了、本命=AivisSpeech 確定。次は P3 (本番統合)。** P5 は声の同一性要件が再優先化した時。

---

## 10. 未確定の方針判断 (Open questions)

1. **トポロジ確定 (§11)** — Pipecat ワーカーと AivisSpeech-GPU をどこで同居させるか (案 A クラウド GPU / 案 B 自前GPU+ワーカー移設)。残る主要論点。
2. **汎用ボイスの確定** — AivisHub の コハク/まお 等の**商用ライセンス確認** + あい/ゆうに合う声/スタイル選定。
3. **自前声 (P5) の学習 base** — permissive/scratch でクリーン化 or 非配布 SaaS で AGPL base 許容 (§15)。
4. **辞書の単一ソース** — 方式は確定 (P1 仕上げ): `aiconv.frontend.dict_sync` がテーマ keyterms
   (`_expand_theme` と同形式) から辞書候補を `data/accent_dict/auto_pending.csv` へ自動生成する。
   pyopenjtalk の誤読リスクがあるため候補は **needs-review** (実行時ロードせず、人間レビューで
   project_words.csv へ昇格 — data/accent_dict/README.md)。残タスク: bot.py `_expand_theme` からの
   配線 (dict_sync.py docstring に手順) と辞書ヒット率の観測 (§13)。
5. **声優音源の調達** — P5 着手時の録音台本・収録量 (100発話〜)・許諾範囲 (`VoiceLicense.allow` の定義)。

> **解決済**: 本命エンジン (§3, P0 実測で **AivisSpeech** 確定) / ライセンス (§15, エンジン=LGPL+ONNX 外部プロセスで AGPL 非伝播) /
> 遅延 (GPU で TTFA 102-124ms, <300ms クリア) / アクセント根治 (P1, 読み 18/18)。

---

## 11. デプロイ・トポロジと可用性 ★レッドチーム最重要

**問題 (レッドチーム §C2/§3.1):** 本番計算が Pipecat Cloud (クラウド)、TTS-GPU が自前 (自宅/オフィス) だと、
(a) 毎合成で WAN 往復が乗り TTFA<300ms が崩れ、(b) 自前回線の分断で**ライブが無音化**する。
ElevenLabs は外部 API なのでこの問題が無かったが、セルフホストは TTS と計算の同居が前提条件になる。

**取りうるトポロジ (P-1 で決定):**
| 案 | 構成 | Pros | Cons |
|---|---|---|---|
| A. クラウド GPU 同居 | Pipecat ワーカー + AivisSpeech Engine を GPU 付きクラウド (同一 region) に同居 | WAN ゼロ・スケール容易・ADR-0003 の思想維持 | GPU クラウドコスト |
| B. 自前 GPU + ワーカー移設 | Pipecat ワーカー自体を自前 GPU ホストで動かす (Pipecat Cloud から自前ホストへ) | 既存 GPU 資産活用・WAN ゼロ | 自前ホスト運用・公開/接続経路・ADR-0003 から逸脱 |
| C. リモート TTS (非推奨) | ワーカーは Pipecat Cloud、TTS だけ自前 GPU を WAN 越しに叩く | 既存構成の最小変更 | WAN 往復・可用性が自前回線依存 (レッドチームが否定) |

**推奨: A (本番) / 検証は B も可**。いずれも **TTS は vLLM(Qwen) と別 GPU/インスタンス** (§C3)。

**プロセス構成 (v5):** AivisSpeech Engine (LGPL) は**外部 HTTP プロセス**として、Pipecat ワーカーと**同一ホストに同居**。
ワーカーは localhost (`:10101`) で VOICEVOX 互換 API を叩く = WAN ゼロ・AGPL 非伝播 (§15) を両立。GPU は AivisSpeech 側。
案 C (WAN 越し) はレイテンシ面で不可 → **案 A/B のみ**。GPU 実測 TTFA 102-124ms (<300ms 達成)。

**可用性 (フォールバック):** TTS 合成のタイムアウト (例 1.5s) で **ElevenLabs へ自動フォールバック**し、
無音を出さない。フォールバック中もフィラーで間を持たせる。未完了ジョブのリソースリーク防止に
合成にデッドラインを設定し、割り込み時は確実にキャンセルする。リトライは**べき等キー** (text+voice+params ハッシュ)
で重複合成を防ぐ。

## 12. セキュリティ・内部脅威 (レッドチーム §4)

- **TTS エンドポイントの認証 (§4.1)**: VOICEVOX を別プロセス HTTP で使う場合や TTS を別プロセス化する場合は
  **認証なしで公開しない**。**localhost (unix socket / ループバック) 限定**にすれば攻撃面は最小。
  AivisSpeech Engine は **localhost (`:10101`) 限定**で起動 (外部公開しない)。外部に出す場合のみ Tailscale ACL / mTLS。
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
- **モデル配布**: AivisSpeech の AIVM モデル + BERT は初回起動時に取得 (AivisHub)。本番はイメージ同梱 or 永続ボリュームで固定。
- **ロールバック**: `TTS_ENGINE` 環境変数で **ElevenLabs ↔ AivisSpeech** を即時切替 (フォールバックと同経路)。
  **辞書変更はデータなので別管理** (辞書の git revert で戻す)。
- **帰属表示**: jvnv (CC BY-SA) / VOICEVOX キャラ規約のクレジットをアプリ内に掲出 (§15)。

---

## 15. ライセンス (P-1 精査結果 — 2026-06-11)

**方針: AGPL の"ネットワークコピーレフトを我々の業務コードに及ぼさない"** (ユーザー決定の精緻化)。
v4 は「SBV2/AivisSpeech= AGPL ゆえ全除外」としたが、**一次確認で AivisSpeech は実質クリーン**と判明し v5 で採用に転じた。

| 層 | コンポーネント | ライセンス | 商用 | 備考 |
|---|---|---|---|---|
| フロントエンド L0/L1 | pyopenjtalk-plus / OpenJTalk / HTS / marine | MIT / BSD / Apache | ✅ | クリーン |
| | naist-jdic 等辞書 | 概ね BSD 系 (要一次確認) | ⚠️ | |
| **音響エンジン (本命)** | **AivisSpeech Engine** | **LGPL-3.0** | ✅ | **ONNX Runtime 推論で litagin の AGPL `style-bert-vits2` を pip 依存に持たない**。外部 HTTP で利用→我々の worker に copyleft 非伝播 |
| エンジン (並列) | VOICEVOX ENGINE / core | LGPL-3.0 / MIT | ✅ | ネット条項なし→SaaS 可。同一 API |
| モデル (バンドル既定) | コハク / まお (AIVMX) | 各 AIVM 個別規約 (**要確認**) | ⚠️ | Anneli は不使用 (声優許諾) |
| モデル (自前声 P5) | SBV2 fine-tune の base 次第 | 下記参照 | ⚠️ | **ここだけ AGPL が残りうる** |
| 対抗 (未採用) | ESPnet2 (Apache) / Kokoro / GPT-SoVITS | Apache / MIT | ✅ | 音質で AivisSpeech に劣後 |

**結論 (v5):**
1. **エンジンは LGPL かつ ONNX 推論で AGPL 推論コードを引かない** → 外部 HTTP プロセスとして使えば**我々の業務コードに AGPL は及ばない**。これが v4 の懸念①を解消し採用に転じた根拠。
2. **唯一 AGPL が残りうるのは「自前声モデルの学習 base」**:
   - litagin の **AGPL 事前学習 base から fine-tune** → 重みが AGPL 派生 (グレー)。
   - **scratch / permissive base から学習** → クリーン。あるいは **SaaS で重みを配布しない**運用なら露出は限定的 (要法務確認)。
   - 学習フレームワーク(SBV2)を**ローカルで回す行為自体は private use で AGPL 非発動**。問題は出発点の base 重みのライセンスのみ。
3. **バンドルの コハク/まお は各 AIVM 個別ライセンス** → 汎用ボイスとして商用利用する前に各モデル規約を確認 (残タスク)。
4. **生成音声は元々コピーレフト非カバー** (プログラム出力)。フロントエンド層は従来どおりクリーン。

**残タスク:**
- **コハク/まお (AIVM) の商用ライセンス確認** (汎用ボイスで使うなら)。
- 自前声 P5 の **学習 base 選択** (permissive/scratch でクリーン化、or 非配布 SaaS で許容) — 着手時に確定。
- naist-jdic 等辞書の商用条項一次確認 / 帰属表示の掲出箇所。

> **P-1 ゲートの AGPL 問題は「エンジン=LGPL+ONNX を外部プロセスで使う」で解消。** 重い法務は不要。
> 残るのは自前声(P5)の学習 base 選択のみで、これは P5 着手時の判断 (採用判断のブロッカーではない)。

---

## 16. 関連

- 上流: [ai-conversation.md](ai-conversation.md) (全体設計, レイテンシ予算, ターンテイキング)
- 統合先コード: `src/aiconv/adapters/tts_elevenlabs.py` (差し替え元), `src/aiconv/core/ports.py` (`TTSProvider`),
  `apps/voice-agent/bot.py` (Pipecat TTS), `apps/conversation-tester/{record_text.py,judge.py,presets.py}`
- 採択時に ADR 化する論点: 「日本語 TTS は OpenJTalk 系フロントエンド + アクセント対応音響モデルを
  セルフホストする」(ADR-0003 の TTS=ElevenLabs を部分 supersede)。
