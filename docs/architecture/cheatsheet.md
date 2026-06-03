# Mermaid Cheatsheet

drive-partner / 他プロジェクトでよく使う Mermaid 記法の早見表。

## 重要: 全図共通の冒頭

すべての Mermaid ブロックの先頭に以下を入れる。GitHub のダーク/ライト両モードで安定した見え方になる。

```
%%{init: {'theme':'default'}}%%
```

加えて、`classDef` で **fill / stroke / stroke-width / color** を全部明示する。`color:#000` を必ず指定するとダークモードでも文字が消えない。

```
classDef boxStyle fill:#90CAF9,stroke:#0D47A1,stroke-width:2px,color:#000
```

## C4-like 図 (Context / Container / Component)

**Mermaid の `C4Context` / `C4Container` / `C4Component` 構文は使わない**。レイアウト崩壊・ラベルオーバーラップが起きやすい既知の問題があるため。代わりに **`flowchart` + `subgraph` + `classDef`** で同等表現する。

### L1 Context (例)

```mermaid
%%{init: {'theme':'default'}}%%
flowchart LR
    classDef person fill:#FFE0B2,stroke:#E65100,stroke-width:2px,color:#000
    classDef system fill:#90CAF9,stroke:#0D47A1,stroke-width:2px,color:#000
    classDef ext fill:#ECEFF1,stroke:#455A64,stroke-width:1px,color:#000

    user(("ユーザー")):::person
    myApp["MyApp<br/>(中心システム)"]:::system

    subgraph external["External"]
        api["外部 API"]:::ext
        db["外部 DB"]:::ext
    end

    user --> myApp
    myApp --> api
    myApp --> db
```

### L2 Container (例)

```mermaid
%%{init: {'theme':'default'}}%%
flowchart TB
    classDef person fill:#FFE0B2,stroke:#E65100,stroke-width:2px,color:#000
    classDef ext fill:#ECEFF1,stroke:#455A64,stroke-width:1px,color:#000
    classDef container fill:#C8E6C9,stroke:#1B5E20,stroke-width:1px,color:#000
    classDef db fill:#FFCCBC,stroke:#BF360C,stroke-width:1px,color:#000

    user(("ユーザー")):::person
    apiExt["外部 API"]:::ext

    subgraph app["MyApp"]
        ui["UI Layer"]:::container
        core["Core Logic"]:::container
        store[("Local Store")]:::db
    end

    user --> ui
    ui --> core
    core --> store
    core --> apiExt
```

### L3 Component (例)

```mermaid
%%{init: {'theme':'default'}}%%
flowchart TB
    classDef coord fill:#FCE4EC,stroke:#880E4F,stroke-width:2px,color:#000
    classDef adapter fill:#E1F5FE,stroke:#01579B,stroke-width:1px,color:#000

    subgraph internal["Core Boundary"]
        coord["Coordinator"]:::coord
        adapter1["AdapterA"]:::adapter
        adapter2["AdapterB"]:::adapter
    end

    coord --> adapter1
    coord --> adapter2
```

## 状態機械

```mermaid
%%{init: {'theme':'default'}}%%
stateDiagram-v2
    [*] --> idle
    idle --> running: start
    running --> idle: stop
    running --> error: failure
    error --> idle: reset
```

ラベルに `\n` を入れるとレンダリングが不安定なことがある。**1 行の短い動詞** で書くのが安全。詳細は表で別途説明する。

## シーケンス

```mermaid
%%{init: {'theme':'default'}}%%
sequenceDiagram
    actor U as ユーザー
    participant A as ComponentA
    participant B as ComponentB

    U->>A: 操作
    A->>B: 委譲
    B-->>A: 応答
    A-->>U: 表示
    Note over A,B: 補足
```

`Note` の中身に `<br/>` で改行可。`\n` は避ける。

## フローチャート (依存・データフロー)

```mermaid
%%{init: {'theme':'default'}}%%
flowchart TD
    classDef step fill:#C8E6C9,stroke:#1B5E20,color:#000
    classDef decision fill:#FFF9C4,stroke:#F57F17,color:#000

    A["Start"]:::step --> B{"Decision"}:::decision
    B -->|"Yes"| C["Action"]:::step
    B -->|"No"| D["Other"]:::step
    C --> E["End"]:::step
    D --> E
```

## クラス図 (必要時のみ)

```mermaid
%%{init: {'theme':'default'}}%%
classDiagram
    class Foo {
        +String name
        +action()
    }
    class Bar
    Foo --> Bar : uses
```

## ER 図 (DB 構造)

```mermaid
%%{init: {'theme':'default'}}%%
erDiagram
    USER ||--o{ POST : writes
    USER {
        UUID id
        string name
    }
    POST {
        UUID id
        UUID user_id
        string title
    }
```

## 色パレット (両モード対応)

ライト / ダーク両方で可読性が確保できる pastel fills + dark text:

| 用途 | fill | stroke |
|---|---|---|
| 人 (Person) | `#FFE0B2` | `#E65100` |
| 中心システム | `#90CAF9` | `#0D47A1` |
| 外部システム | `#ECEFF1` | `#455A64` |
| Container | `#C8E6C9` | `#1B5E20` |
| Component (中心) | `#FCE4EC` | `#880E4F` |
| Adapter / 周辺 | `#E1F5FE` | `#01579B` |
| 補助 | `#FFF9C4` | `#F57F17` |
| DB / store | `#FFCCBC` | `#BF360C` |
| Infrastructure | `#F3E5F5` | `#4A148C` |

文字色は **常に `#000`** で固定。

## Tips

- GitHub の PR / Issue / README で **そのままレンダリング**
- 描画が崩れる場合は VS Code の Mermaid プレビューで確認
- 長すぎる説明はノード内ではなく **本文側に箇条書き** で記す
- 矢印のラベルは引用符付き (`|"text"|`) で安全側に
