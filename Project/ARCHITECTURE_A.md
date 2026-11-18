# ARCHITECTURE_A.md — A案「堅実」（Go ＋ ローカルWeb UI）

## 1. コンセプト
「小さく、単純で、いつも同じ結果を返す」。機能は必要十分に絞り、失敗時は**安全側（検査を止める・入力を変更しない）**に倒す。結果の再現性を**実行証明マニフェスト**で担保する。

## 2. 設計思想
- 依存を最小化（標準ライブラリ中心）。CGOなし。単一の静的EXE。
- ロジック（`internal/*`）とUI（`internal/web`）を分離。検査コアはI/O（ファイル）以外は純粋関数に近づける。
- 例外ではなくエラー値で制御。`panic`は`recover`でトップレベル捕捉し、診断（値を含まない）を残して安全終了。
- fail-closed: ルール不正・設定不明なら検査を開始しない。

## 3. 技術選定（バージョン固定・GPTの知識時点に合わせる）
| 用途 | 採用 | 備考 |
|---|---|---|
| 言語 | Go 1.22（`go.mod`は`go 1.22`） | コンテナのGo 1.24でもビルド可 |
| 文字コード | `golang.org/x/text` v0.14.0（`encoding/japanese`: ShiftJIS=CP932互換は`japanese.ShiftJIS`、**CP932拡張(①㈱髙﨑等)の扱いは検品で要検証**） | 不足なら自前テーブルは使わず、検証結果で対応を決める |
| Excel | `github.com/xuri/excelize/v2` v2.8.1 | BSD-3 |
| 正規表現 | 標準`regexp`（RE2・線形時間） | |
| JSON/ハッシュ/HTTP | 標準 `encoding/json`, `crypto/sha256`, `net/http`, `embed` | |
| UI | 標準`html/template`＋素のHTML/CSS/JS（フレームワーク無し）。`embed`で同梱 | 外部CDN禁止 |
| 履歴 | JSONファイル（`data/history/<runid>.json`） | |

## 4. ディレクトリ構成
```
Project/A/
  go.mod
  cmd/csvqc/main.go                 # A-09: エントリ（--headless / GUI起動）
  internal/model/model.go           # A-01: Finding, Severity, ルールID定数
  internal/rules/rules.go           # A-02: Ruleset型, Load, Validate, Default
  internal/rules/pattern.go         # A-01: 正規表現の静的安全チェック
  internal/rules/dateformat.go      # A-01: 日付書式トークンの検証・既知書式
  internal/csvin/sniff.go           # A-03: 文字コード判定・デコーダ
  internal/csvin/parser.go          # A-03: ストリーミングCSVパーサ(状態機械)
  internal/check/cell.go            # A-04: E001,E002,E009,E010,E011,E016,E014(pattern/enum)
  internal/check/typed.go           # A-05: E005,E006,E007,E008
  internal/check/record.go          # A-06: E012,E013,E015
  internal/engine/engine.go         # A-07: パイプライン、E003/E004、集計、打ち切り、キャンセル
  internal/report/{csv,xlsx,html}.go# A-08
  internal/suggest/suggest.go       # A-08: message/suggestion文言、修正候補(OPT-08)
  internal/store/{history,templates}.go # A-10
  internal/web/{server.go,handlers.go,static/} # A-11, A-12
  internal/manifest/manifest.go     # A-14: 実行証明
  internal/lint/lint.go             # A-14: ルールlint
  .github/workflows → リポジトリ直下の .github/workflows/csvqc-a.yml (A-13)
```
モジュール名は `csvqc`。インポートは `csvqc/internal/...`。

## 5. 中核の型（凍結。各プロンプトに全文を埋め込む）
```go
// package model
type Severity string
const (
	SeverityError   Severity = "error"
	SeverityWarning Severity = "warning"
)
const ( // ルールID
	E001 = "E001"; E002 = "E002"; E003 = "E003"; E004 = "E004"; E005 = "E005"
	E006 = "E006"; E007 = "E007"; E008 = "E008"; E009 = "E009"; E010 = "E010"
	E011 = "E011"; E012 = "E012"; E013 = "E013"; E014 = "E014"; E015 = "E015"
	E016 = "E016"; E017 = "E017"; W101 = "W101"
)
type Finding struct {
	RuleID            string   `json:"rule_id"`
	RecordNo          int      `json:"record_no"`
	LineNo            int      `json:"line_no"`
	Column            *string  `json:"column"`
	ColumnIndex       *int     `json:"column_index"`
	Value             *string  `json:"value"`
	Severity          Severity `json:"severity"`
	Message           string   `json:"message"`
	Suggestion        string   `json:"suggestion"`
	FirstSeenRecordNo *int     `json:"first_seen_record_no,omitempty"`
}
```
```go
// package rules  — 公開型は「解決済み(既定値適用済み)」。JSONを直接受ける型は非公開(raw*)で、Loadが変換する。
type ValidationError struct{ Path, Code, Message string } // INTERFACE_SPEC §3.5

type ColumnType string
const (
	TypeString   ColumnType = "string"
	TypeInteger  ColumnType = "integer"
	TypeDecimal  ColumnType = "decimal"
	TypeDate     ColumnType = "date"
	TypeDatetime ColumnType = "datetime"
)
type CSVOptions struct {
	HasHeader bool
	Delimiter rune
}
type NumberOptions struct{ AllowThousandsSeparator, AllowFullwidthDigits bool }
type CheckFlags struct{ Whitespace, SuspiciousChars, Mojibake bool }
type ColumnRule struct {
	Name          string
	Type          ColumnType
	Required      bool
	MaxLength     *int
	Pattern       *string
	PatternRE     *regexp.Regexp // "^(?:" + pattern + ")$" をコンパイル済み。Pattern==nilならnil
	AllowedValues []string       // nilなら制約なし
	Min, Max      *float64
	MinDate, MaxDate *string     // "yyyy-MM-dd"
	DateFormats   []string       // date/datetime型: 既定値適用済み。他の型ではnil
	Number        NumberOptions
	Checks        CheckFlags
}
type DuplicateRule struct {
	ID      string
	Columns []string
}
type When struct {
	Column, Op string
	Value      string
	Values     []string
}
type CrossRule struct {
	ID, Type    string   // Type: compare | required_if | range_if | sum_equals
	Left, Op, Right string // compare
	Column      string   // required_if, range_if
	Min, Max    *float64 // range_if
	Columns     []string // sum_equals
	Total       string   // sum_equals
	Tolerance   float64  // sum_equals
	When        *When    // required_if, range_if
}
type SuspiciousOptions struct{ Control, ZeroWidth, PrivateUse bool }
type OutlierOptions struct {
	Enabled bool
	Method  string
	K       float64
}
type Options struct {
	NullTokens  []string
	Suspicious  SuspiciousOptions
	Outlier     OutlierOptions
	MaxFindings int
}
type Ruleset struct {
	SchemaVersion         int
	Name                  string
	CSV                   CSVOptions
	Columns               []ColumnRule
	Duplicates            []DuplicateRule
	CheckDuplicateRecords bool
	CrossRules            []CrossRule
	Options               Options
	Extensions            map[string]json.RawMessage
}

func Default() *Ruleset                                       // INTERFACE_SPEC §3.6 の既定ルールセット
func Load(data []byte) (*Ruleset, []ValidationError)           // 検証し全エラーを返す。エラーがあればRulesetはnil
```

## 6. データフロー
```
file(read-only) → csvin.Sniff → decoder → csvin.Parser(record stream: RecordNo, LineNo, []string, flags)
   → engine: per record { check.Cell… , check.Record…, dup(E003/E004) , cross(E015) } → findings(sorted at end/stream order)
   → report/* ・ manifest ・ store/history
UI(web) ←→ engine via in-process calls; progress via SSE or polling; cancel via context.Context
```
- 全レコードを保持しない。重複検出は`map[[16]byte]int`（レコード/キーのハッシュ → 初出record_no）。**衝突時は誤検出を避けるため、ハッシュ一致後に初出レコードの再比較が必要**だが、全行保持しないため、**128bit(SHA-256の先頭16バイト)ハッシュ一致を重複とみなす**（衝突確率は無視可能）と割り切り、ARCHITECTUREに明記（リスクとして記録）。

## 7. セキュリティ設計（ローカルHTTPサーバ）
- `127.0.0.1`のみにbind、ポートはOS採番、起動ごとに256bitランダムトークン。URLに`?t=<token>`で最初のアクセスを認可し、以後は`HttpOnly; SameSite=Strict`のCookieで認可。全APIでトークン/Cookie必須。
- `Host`ヘッダが`127.0.0.1:<port>`でなければ拒否（DNSリバインディング対策）。`Origin`/`Sec-Fetch-Site`チェック（POSTは同一オリジンのみ）。CORS無し。
- 厳格なCSP（`default-src 'self'`）、`X-Content-Type-Options: nosniff`、`Cache-Control: no-store`。
- ブラウザタブを閉じる/ハートビート切れ（例60秒）でサーバ終了。
- **ファイルの受け渡し**: ブラウザの`<input type=file>`はローカルパスを渡さない。そこでブラウザが選んだファイルを**HTTPボディとしてストリームでサーバへ送り、サーバは一時ファイルを作らずそのままストリーム解析する**（メモリ・ディスクに実データのコピーを残さない: NFR-SEC-08）。サーバが保持するのはファイル名・サイズ・SHA-256のみ（パスは保持しない）。500MBのループバック転送は許容範囲と仮定し、A-11で実測する。
- 元ファイルはブラウザが読むだけで、サーバ側から元ファイルへ書き込む経路が存在しない（NFR-SEC-01を構造的に満たす）。出力ファイルは`reports/`（EXE隣）への保存、またはブラウザのダウンロードとして提供する。

## 8. メリット / デメリット / リスク
- メリット: 依存少、単一EXE、ビルド再現性高、UIをPlaywrightで実検証可能、監査しやすい。
- デメリット: UIが素朴、ブラウザ依存（既定ブラウザが古いと動作不能）、「デスクトップアプリ」らしさが弱い。
- リスク: ローカルHTTPサーバの攻撃面（§7で緩和）、CP932拡張文字のx/text対応（検品で実測）、ファイル500MBのアップロード方式の性能。

## 9. 独自機能（A-14）
- **実行証明マニフェスト**: `manifest.json` に 入力SHA-256・ルールSHA-256・結果SHA-256・ツール版・時刻・各ハッシュ連鎖（結果ハッシュが他2つを含む）を記録。`csvqc verify <manifest> <csv> <rules>` で再検査して一致を検証。
- **ルールlint**: 矛盾（min>max）、到達不能、危険パターン、列名のtypoらしき近似（編集距離）を警告。

## 10. タスク一覧
TASK_REGISTRY.md の A-01〜A-14 を参照。

---
## 変更履歴
| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 | 2026-11-18 | 初版 |
