# accent_dict — 日本語 TTS フロントエンドのユーザー辞書 (単一ソース)

固有名詞・キャラ名・プロジェクト語彙の **読みとアクセントを 100% 固定** するための
OpenJTalk (NAIST-JDIC) 形式ユーザー辞書。設計: docs/design/japanese-tts-optimization.md §5-L1。

このディレクトリの `*.csv` がソースの全て。バイナリ (`.dic`) はコミットしない
(実行時にプロセス毎の一時ディレクトリへコンパイルされる)。

| ファイル | 役割 | 実行時ロード |
|---|---|---|
| `project_words.csv` | curated 辞書 (人間レビュー済み。発話に反映される) | する |
| `auto_pending.csv` | dict_sync の自動候補 (**needs-review**。読み未検証) | **しない** |

## ビルド / ロード

`aiconv.frontend.predict_accent()` の初回呼び出しで **自動適用** される
(コンパイル → `pyopenjtalk.update_global_jtalk_with_user_dict()`)。手動制御:

```python
from aiconv.frontend import load_user_dict, reset_user_dict

load_user_dict()                 # data/accent_dict/*.csv をコンパイルして適用
load_user_dict(Path("別dir"))    # ディレクトリ指定
reset_user_dict()                # 解除 (素の NAIST-JDIC に戻す)
```

- ディレクトリ探索: 環境変数 `AICONV_ACCENT_DICT_DIR` → cwd から上方 → パッケージ位置から上方。
- 検証: `uv run python apps/tts-bench/run_bench.py --dry-run` と
  `uv run pytest tests/test_tts_frontend.py` が読み/アクセントの回帰テストを兼ねる。

## CSV 形式 (NAIST-JDIC / MeCab 15 カラム)

```
表層形,左文脈ID,右文脈ID,コスト,品詞,品詞細分類1,品詞細分類2,品詞細分類3,活用型,活用形,原形,読み,発音,アクセント型/モーラ数,アクセント結合規則
宮崎駿,,,8609,名詞,固有名詞,人名,一般,*,*,宮崎駿,ミヤザキハヤオ,ミヤザキハヤオ,5/7,*
```

- **左/右文脈ID**: 空欄でコンパイラが自動割当。
- **コスト**: 小さいほど採用されやすい。固有名詞は 8609 を目安に、既存語に勝てない場合は下げる。
- **発音**: 長音は「ー」(ユウクン → ユークン)。
- **アクセント型/モーラ数**: `核位置/モーラ数`。核位置 0 = 平板
  (例: `5/7` = 7 モーラの 5 モーラ目で下がる = ミヤザキハ↓ヤオ)。
- **アクセント結合規則**: 通常 `*`。

## 追加のルール (PR 運用)

1. 1 行 1 語で追記し、**期待読みの根拠** (公式表記・NHK アクセント辞典等) を PR 本文に書く。
2. 読み・アクセントの変更は **人間レビュー必須** (設計のレッドチーム指摘:
   辞書改変はそのまま発話になるため、内部脅威・タイポの双方を防ぐ)。
3. 追加したらテスト文 (apps/tts-bench/test_sentences.py) か
   tests/test_tts_frontend.py に期待読みを足して回帰で守る。

## auto_pending.csv — テーマ keyterms からの自動候補 (needs-review)

テーマ前提知識 (`_expand_theme` の keyterms, bot.py) と同じ語彙源から辞書候補を自動生成する
(設計 §10-4 の自動同期。実装: `src/aiconv/frontend/dict_sync.py`):

```bash
uv run python -m aiconv.frontend.dict_sync --keyterms "宮崎駿, スタジオジブリ, 大谷翔平"
uv run python -m aiconv.frontend.dict_sync --keyterms-file keyterms.txt
```

- 各 keyterm の **読み候補・モーラ数・アクセント核** を pyopenjtalk で推定し、
  project_words.csv と同じ 15 カラム形式 (品詞=`名詞,固有名詞,一般`) の行を
  `auto_pending.csv` に**追記**する。既存辞書 (curated + pending) に表層がある語は
  スキップするため再実行は冪等。
- **pyopenjtalk は未知の固有名詞を誤読する** (宮崎駿→シュン、米津玄師→ヨネツゲンシ)。
  だから候補はサイレントに信用せず、`load_user_dict()` は auto_pending.csv を
  **ロードしない** (needs-review の隔離)。
- 英字のみの keyterm は候補化しない (L0 `KNOWN_WORDS` の担当。下記「登録のコツ」)。
- bot.py への配線はフォローアップ: `_expand_theme` が keyterms を得た直後に
  `aiconv.frontend.sync_keyterms(kt)` を呼ぶ (失敗しても会話本体を止めないこと。
  詳細は dict_sync.py の docstring)。

### 昇格 (auto_pending.csv → project_words.csv)

1. `auto_pending.csv` の行の **読み・アクセントを人間が検証** する
   (公式表記・NHK アクセント辞典等。誤りは行を直す)。
2. 検証済みの行を `project_words.csv` へ移動し、`auto_pending.csv` から削除する。
   誤読しない語 (素通しで正しく読める語) は登録せず行を削除するだけでよい
   (「登録のコツ」参照。姓+名の併合で韻律が単調になる副作用がある)。
3. 上記「追加のルール (PR 運用)」に従い、根拠を PR 本文に書き、回帰テストを足す。

## 登録のコツ / 落とし穴

- **ひらがな単独の短い語は登録しない**。例: キャラ名「あい」を `あい` のまま登録すると
  「じゃ**あい**こう」「会**い**たい」等の一部を人名として乗っ取る。
  `あいちゃん`「ゆうくん」のように呼称付きの表層で登録する。
- **姓+名をまとめて 1 語にすると 1 アクセント句に併合される** (ネイティブの
  「姓 / 名」2 句読みより韻律が単調になる)。誤読しない人名 (新海誠・大谷翔平等は
  NAIST-JDIC が正しく読む) は登録しない。登録するのは誤読する語だけ
  (宮崎駿 → 素通しではミヤザキ「シュン」)。
- 英単語の読み (OpenAI → オープンエーアイ等) はここではなく **L0 の既知語辞書**
  (`src/aiconv/frontend/text_normalize.py` の `KNOWN_WORDS`) に登録する
  (MeCab 辞書は ASCII 表層の扱いが不安定なため)。
- テーマ語彙 (bot.py `_expand_theme` の keyterms) との自動同期は `dict_sync`
  (上記「auto_pending.csv」) で行う。自動候補は人間レビューを経て昇格する。
