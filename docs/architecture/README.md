# Architecture

C4 model + 補足図でアーキテクチャを表現。Mermaid 記述、GitHub レンダリング。

## C4 model レベル

| レベル | 想定ファイル | 内容 |
|---|---|---|
| L1 Context | `0-context.md` | システム境界。ユーザー / アプリ / 外部システム |
| L2 Container | `1-containers.md` | アプリ内の主要 container (主要モジュール) |
| L3 Component | `2-components.md` | Container 内のコンポーネント結線 |
| L4 Code | (任意、必要時のみ) | 個別クラス・関数の関係 |

## 補足図 (必要に応じて追加)

| 推奨ファイル名 | 内容 | Mermaid 種 |
|---|---|---|
| `3-state-machine.md` | 状態機械 | `stateDiagram-v2` |
| `4-sequence-<名>.md` | 主要シナリオのシーケンス | `sequenceDiagram` |
| `5-data-flow-<名>.md` | データフロー | `flowchart` |
| `6-module-dependencies.md` | モジュール依存 | `flowchart` |

## 凡例

詳しい記法は [cheatsheet.md](cheatsheet.md) を参照。

## 運用

- **設計変更時の更新順**: ADR (なぜ) → architecture (どう動くか) → コード (実装)
- **drift 検出**: 大きな変更後に `/docs-sync` スキルで図とコードのズレを確認
- **議論の起点**: PR レビューで Mermaid 図が GitHub 上でレンダリングされ、視覚的にレビュー可能
