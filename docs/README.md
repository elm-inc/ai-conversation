# Documentation (テンプレ)

このプロジェクトのドキュメント基盤。文書化方針は `~/CLAUDE.md` (= `elm-inc/agent-rules/CLAUDE.md`) を参照。

## 構成

| ディレクトリ | 役割 |
|---|---|
| `adr/` | Architecture Decision Records — なぜ・何を決めたか |
| `architecture/` | C4 model + 状態/シーケンス/依存図 — どう動くか |
| `design/` | 実装計画など — これから何をどう作るか |

## 原則

- **真理の単一源は git**。Notion / Confluence / Linear Docs 等の SaaS には設計図を置かない
- **すべて Markdown + Mermaid** で記述。GitHub が直接レンダリング
- **AI が完結して触れる**。Claude Code が Read/Edit/Write でフル制御可
- **コードと一緒に進化する**。設計変更時は ADR → 図 → コード の順で更新
