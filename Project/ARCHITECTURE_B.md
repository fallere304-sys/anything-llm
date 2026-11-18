# ARCHITECTURE_B.md — B案「高機能・拡張性」（TypeScript ＋ Electron）

## 1. コンセプト
「ルールも出力も解析も差し替えられるプラットフォーム」。検査ルールを**プラグイン**（登録式モジュール）として扱い、項目間ルールは**宣言的な式DSL**で表現する。複数ファイル一括検査・結果比較・テンプレート管理・Analyzer拡張点（将来のローカルAI分析）を標準で備える。

## 2. 設計思想
- **コア(`core/`)はElectronに依存しない純Node/TypeScript**。CLI・GUI・テストが同一コアを使う。
- ルールは `RulePlugin` インターフェース（`id`, `severity`, `scope`, `evaluate`）に統一。組み込みルールも同じ仕組みで登録（拡張性のドッグフーディング）。
- 外部プラグインの動的読込は**既定で無効**（任意コード実行になるため）。同梱プラグインのみ有効。これはFR-RULE-10（ルールにコードを含めない）の維持。
- **式DSL**: `入院日 <= 退院日 and (状態 == "退院" implies 退院日 != "")` のような式を**自前パーサ（字句解析→構文解析→AST→評価）**で実装。`eval`/`Function`禁止。INTERFACE_SPECのcross_rules(v1)は**DSLの特殊形としてコンパイル**して契約と同一の結果を保証する。DSL固有の追加ルールは `extensions.b_dsl_rules` に記述し、違反は契約どおり **E015** として報告する（rule_idを独自に増やさない）。

## 3. 技術選定（固定）
| 用途 | 採用 |
|---|---|
| 言語 | TypeScript 5.5、Node 20系API（Electron 31同梱） |
| GUI | Electron 31（`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`、preload経由IPC、CSP厳格化） |
| UI | 素のHTML/CSS＋TypeScript（フレームワーク無し。仮想スクロールは自前） |
| 文字コード | iconv-lite 0.6.3 |
| Excel | exceljs 4.4.0 |
| テスト | vitest 1.6 |
| 履歴/テンプレ | JSONファイル＋索引（ネイティブモジュール(SQLite)は電子再ビルド問題を避けるため不採用） |
| 配布 | electron-builder 24.x の `dir` ターゲット→ZIP（CI。詳細はB-12で検証） |
| 正規表現 | JSの`RegExp`はバックトラック型 → **INTERFACE_SPEC §3.4 の静的安全チェックを必須**、さらに評価前に値長を4096文字で打ち切り判定（超過は E014 形式不一致） |

## 4. 構成
```
Project/B/
  package.json, tsconfig.json, vitest.config.ts
  src/core/model.ts            # B-01: 型
  src/core/rules/loader.ts     # B-02: rules.json検証
  src/core/rules/pattern.ts    # B-01: 静的安全チェック
  src/core/io/{sniff,parser}.ts# B-03
  src/core/plugins/registry.ts # B-04: RulePlugin, Registry
  src/core/plugins/builtin/*.ts# B-04〜B-06: E001…E017を1ファイル1ルール(または小グループ)
  src/core/dsl/{lexer,parser,eval}.ts # B-14
  src/core/engine.ts           # B-07
  src/core/report/*.ts         # B-08
  src/core/batch.ts, compare.ts# B-15
  src/core/analyzers/api.ts    # B-16: Analyzerインターフェース＋サンプル(ローカル集計のみ)
  src/cli/main.ts              # B-09
  src/main/ (Electron main), src/preload/, src/renderer/ # B-11, B-12
  src/core/store/*.ts          # B-10
```

## 5. 主要インターフェース案（B-03で凍結）
```ts
export type Severity = "error" | "warning";
export interface CellContext { value: string; trimmed: string; column: ResolvedColumn; recordNo: number; lineNo: number; ruleset: Ruleset; }
export interface RulePlugin { readonly id: string; readonly severity: Severity; readonly scope: "cell" | "record" | "file"; }
export interface CellRule extends RulePlugin { scope: "cell"; evaluate(ctx: CellContext): Finding | null; }
export interface RecordRule extends RulePlugin { scope: "record"; evaluate(rec: RecordContext): Finding[]; }
export interface Analyzer { readonly id: string; analyze(result: ResultSummary): AnalysisNote[]; } // ローカル実行のみ。ネットワーク禁止
```
セル評価の**順序・スキップ規則はINTERFACE_SPEC §5.2が優先**。プラグインは順序つき配列としてEngineが保持。

## 6. メリット / デメリット / リスク
- メリット: 拡張が容易、機能が豊富（希望機能を広くカバー）、Chromium同梱でWebView2問題が無い。
- デメリット: 配布サイズ大（~200MB）、起動やや遅い、抽象化で構造が複雑になりGPTの出力整合が崩れやすい。
- リスク: **電子のコンテナ内実行が未検証**（Linux+ヘッドレス）、electron-builderの`dir`出力CI。DSLの誤評価（式優先順位）、JS正規表現のReDoS、プラグイン抽象化によるバグ増。
- 緩和: コアは純Nodeでvitest。DSLは文法とテストケースを仕様化。静的パターンチェック＋値長上限。

## 7. 独自機能（B-14〜B-16）
式DSL(B-14)／一括検査・結果比較(B-15)／Analyzer拡張点＋ローカル集計サンプル(B-16)／テンプレート管理・履歴(B-10, B-12)。

## 8. 検証計画
`vitest`でコア全体＋契約テスト（`/Project/eval`の共通コーパス）。レンダラはPlaywrightで`file://`のHTMLを（preloadのモックを注入して）操作して検証する方法を検討（B-10の成果物の形で決める）。Electron実体はCI(Windows)でスモーク起動し、ユーザー実機で最終確認。

---
## 変更履歴
| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 | 2026-11-18 | 初版 |
