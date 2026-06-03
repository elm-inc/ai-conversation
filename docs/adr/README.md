# Architecture Decision Records

アーキテクチャ上の決定を ADR として記録する。

## 運用ルール

- ファイル名: `NNNN-kebab-case-title.md` (NNNN は通し番号、4 桁ゼロ埋め)
- 採番: 1 から連番、**欠番なし**
- 採択した ADR は **書き換えない**。変更は新しい ADR で旧 ADR を Supersede
- 採択日は ISO 形式 (`YYYY-MM-DD`)
- 関連 ADR は **相互リンク**

## 採番ヘルパー

```bash
ls docs/adr/[0-9]*.md 2>/dev/null | sed -n 's|.*/0*\([0-9]*\)-.*|\1|p' | sort -n | tail -1
```

`/adr-new <title>` スキルで自動採番＋テンプレ展開できる。

## 索引

(各プロジェクトでここに追記)
