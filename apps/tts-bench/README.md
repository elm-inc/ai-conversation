# tts-bench — 日本語 TTS エンジン比較ハーネス (AIC-8 P0)

ElevenLabs の日本語ピッチアクセント問題 (平板読み) の根治に向け、アクセントを明示制御できる
日本語特化 TTS 候補を同一テストセットで比較する (設計: docs/design/japanese-tts-optimization.md)。

- 比較軸: **アクセント正確性** (フロントエンド層) / **自然さ** (聴取シート) /
  **TTFA** (目標 < 300ms) / **RTF** / **VRAM**
- 候補: ESPnet2 VITS (本命) / VOICEVOX (並列) / Kokoro-82M (即時汎用) / ElevenLabs (現行基準)

## クイックスタート

```bash
# 1) フロントエンド層 (pyopenjtalk-plus) を導入して配線検証
uv sync --inexact --extra tts-bench
uv run python apps/tts-bench/run_bench.py --dry-run

# 2) 使いたいエンジンを導入して本計測 (利用可能なものだけ自動で対象になる)
uv run python apps/tts-bench/run_bench.py
uv run python apps/tts-bench/run_bench.py --engines elevenlabs,voicevox
uv run python apps/tts-bench/run_bench.py --judge          # LLM 読みサニティ付き
uv run python apps/tts-bench/run_bench.py --ids mp-ame-rain,mp-hashi-bridge
```

出力は `apps/tts-bench/out/` に:

- `report.md` — エンジン比較サマリ / アクセントチェック / 聴取シート (人手採点欄)
- `results.csv` — エンジン × 文の生計測 (TTFA/RTF/VRAM/音声長)
- `<engine>/<id>.wav` — 合成音声 (聴取シートから参照)

エンジンが未導入・未起動でもハーネスは止まらず、理由つきで skip して残りを計測する。

## エンジン別セットアップ

### ElevenLabs (現行基準)

```bash
uv sync --inexact --extra providers
echo "<APIキー>" > ~/.elevenlabs_token   # または ELEVENLABS_API_KEY
```

| 環境変数 | 既定 |
|---|---|
| `ELEVENLABS_VOICE_ID` | あい本番ボイス (presets.py の AI_VOICE_ID と同値) |
| `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` |

### ESPnet2 VITS (本命)

```bash
uv sync --inexact --extra tts-bench --extra tts-bench-espnet
```

- 既定モデル: `kan-bayashi/jsut_vits_accent_with_pause`
  (G2P=`pyopenjtalk_accent_with_pause`、JSUT 事前学習 VITS)。初回実行時に
  espnet_model_zoo (Zenodo/HF) から自動ダウンロードされる。
- `ESPNET_MODEL` でタグ差し替え可 (例: `kan-bayashi/jsut_full_band_vits_prosody` =
  韻律記号 G2P の 44.1kHz 版)。`ESPNET_DEVICE=cuda` で GPU 推論。
- 話者適応 (あい/ゆう声へのファインチューン) は P1 でこの事前学習を起点にする。

### VOICEVOX (並列候補)

追加の Python 依存なし (HTTP)。ローカルで VOICEVOX ENGINE を起動しておく:

```bash
# Docker (CPU)
docker run --rm -p 127.0.0.1:50021:50021 voicevox/voicevox_engine:cpu-latest
# または公式配布の ENGINE/エディタを起動 (https://voicevox.hiroshiba.jp/)
```

| 環境変数 | 既定 |
|---|---|
| `VOICEVOX_URL` | `http://127.0.0.1:50021` |
| `VOICEVOX_SPEAKER` | `3` (ずんだもん ノーマル。`GET /speakers` で一覧) |

tamayori-tts の VOICEVOX ONNX 運用と同じ API (audio_query → synthesis)。アクセント編集
(audio_query の `accent_phrases` 書き換え) も同 API で可能。

### Kokoro-82M (即時汎用ボイス)

```bash
uv sync --inexact --extra tts-bench-kokoro
uv run python -m unidic download   # misaki[ja] の形態素辞書 (初回のみ、~1GB)
```

- 重み (hexgrad/Kokoro-82M) は初回に Hugging Face から自動ダウンロード。
- `KOKORO_VOICE` 既定 `jf_alpha` (他: jf_gongitsune / jf_nezumi / jf_tebukuro / jm_kumo)。
- **注意**: misaki[ja] は本家 pyopenjtalk に依存し、`tts-bench` extra の pyopenjtalk-plus と
  同一モジュール名で衝突するため **同時インストール不可** (pyproject の `[tool.uv]
  conflicts` で禁止)。kokoro を測るときは `--extra tts-bench` を外す (本家 pyopenjtalk が
  入るのでアクセントチェックはそのまま動く)。

### LLM 読みサニティ (--judge)

```bash
echo "<APIキー>" > ~/.anthropic_token   # または ANTHROPIC_API_KEY
uv run python apps/tts-bench/run_bench.py --dry-run --judge
```

pyopenjtalk の予測読み/アクセントを LLM が校正者として検査する (固有名詞の読み崩れ検出)。
音声そのものの「自然さ」採点は `report.md` の聴取シートに人手で記入する
(Anthropic API は音声入力非対応のため。将来 STT roundtrip 等で自動化予定)。

## テストセット

`test_sentences.py` — 18 文、5 カテゴリ (最小対立 / 固有名詞 / キャラ名 / 長文 / L0 正規化)。

- `expected_accent` は「アクセント句 読み[核位置]」表記 (0=平板)。初期値は
  pyopenjtalk-plus 予測ベース + NHK アクセント辞典との照合。**人手 (音声) 検証は未了**。
- 正規化カテゴリは現状の素通しで **fail するものを含む** (例: 「3,000円」→
  サン、ゼロゼロゼロエン)。これは L0 正規化レイヤが必要な根拠の固定化であり、
  正規化実装後に green になるべき回帰テスト。

## ライセンス注記 (重要)

| エンジン / 部品 | ライセンス | 注意 |
|---|---|---|
| ESPnet (コード) | Apache-2.0 | 事前学習モデルは**学習コーパス規約**に従う: JSUT は非商用配布・**商用利用は要連絡** (ライセンス上の明示許諾を得るまで本番声には使わない)。jvnv 系は CC BY-SA 4.0 (**クレジット表示 + 継承**) |
| VOICEVOX ENGINE | LGPL-3.0 (コアは MIT) | HTTP プロセス分離で利用 (コードへの伝播なし)。**音声利用はキャラクターごとの利用規約**に従い「VOICEVOX:ずんだもん」等の**クレジット掲出が必要** |
| Kokoro-82M | Apache-2.0 (コード・重みとも) | 学習データ由来の制約なし (公称)。商用可 |
| pyopenjtalk-plus / marine | MIT / Apache-2.0 | フロントエンド層。商用クリーン |
| ElevenLabs | 商用 API | 既存契約に従う |

**Style-Bert-VITS2 / AivisSpeech は使用しない**: コードも配布重みも AGPL-3.0 であり、
ネットワークサービスへの組込みでソース開示義務が発生するため、本プロジェクトでは
依存に入れることを禁止する (設計 docs/design/japanese-tts-optimization.md の確定方針)。

## 開発 (lint / 型 / テスト)

```bash
uv run ruff check apps/tts-bench tests/test_tts_bench.py
MYPYPATH=src:apps/tts-bench uv run mypy --strict apps/tts-bench
uv run pytest -q tests/test_tts_bench.py   # pyopenjtalk 無し環境では accent テストが skip
```

## トラブルシュート

- `pyopenjtalk 未導入` → `uv sync --inexact --extra tts-bench`。import 時の
  「ONNX Runtime is not installed」警告は無害 (marine の何点推定が無効になるだけ)。
- ESPnet の初回が遅い → モデルダウンロード (数百 MB)。2 回目以降はキャッシュ。
- ESPnet のモデルタグが見つからない → `espnet_model_zoo` の対応表
  (https://github.com/espnet/espnet_model_zoo) で `jsut` を検索。
- kokoro で `unidic` エラー → `uv run python -m unidic download` を実行したか確認。
- VOICEVOX `接続できない` → ENGINE が 50021 で起動しているか `curl localhost:50021/version`。
