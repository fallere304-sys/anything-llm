# INTERFACE_SPEC.md — 共通契約（全案・評価基盤共通）v1.0

**この文書は凍結契約**。変更できるのはClaude（CTO）のみで、変更時は版を上げ全案に再通知する。各案の実装コードは共有しないが、**この契約（入出力・検査の意味）は全案が同一に実装する**。これが「同じ基準で機械的に比較する」ための前提である。

用語: `record_no`=データレコードの1始まり通し番号（ヘッダー除く）。`line_no`=そのレコードが始まる物理行番号（ヘッダー行=1）。`column_index`=1始まりの列位置。

---
## 1. ヘッドレスCLI契約
```
<exe> --headless --input <csv_path> --output <findings.json>
      [--rules <rules.json>] [--encoding auto|utf-8|cp932] [--report-dir <dir>]
```
- `--rules` 省略時は §3.6 の既定ルールセット。`--encoding` 既定 `auto`。
- 終了コード: `0`=検査完了（異常の有無を問わない）/ `2`=入力ファイルエラー（無い・開けない・ディレクトリ等）/ `3`=ルールファイル不正 / `4`=内部エラー。
- `--output` が `--input` と同一パスなら終了コード2。既存ファイルへの上書きはヘッドレスでは許可。
- 標準出力・標準エラーに**CSVの実データ値を出してはならない**。
- `--output` の `findings.json` は §4。終了コード3のときは `{"schema_version":1,"rules_errors":[{"path","code","message"}]}` を書く。

## 2. 解析仕様（CSV・文字コード）
### 2.1 文字コード判定（`auto`の基準アルゴリズム）
1. 先頭が `EF BB BF` → `utf-8-bom`（BOMは除去）。
2. 先頭**4MiB**（または全体）が厳密なUTF-8として有効 → `utf-8`。全てASCIIのときも `utf-8`。
3. 上記でなければ `cp932`（Shift_JIS拡張。WHATWG/Windows-31J相当）。
4. `--encoding utf-8|cp932` 指定時はそれを使用。BOM付き指定`utf-8`でBOMがあれば除去。
- 不正バイトは**デコード失敗にせず U+FFFD に置換**して処理を継続する（E011 の根拠になる）。
- `encoding_confidence`: `high`（BOMあり、またはUTF-8で非ASCII含む、または`--encoding`指定）／`medium`（全てASCII）／`low`（cp932フォールバックで置換文字が発生）。

### 2.2 CSV構文
- 区切り: `csv.delimiter`（既定`,`、1文字）。引用符: `"`。レコード区切り: CRLF / LF / CR（引用符の外）。
- 引用符内の区切り・改行・`""`（→`"`）を許容。フィールド先頭の`"`で引用フィールド開始、閉じ引用符後にその場で区切り/改行/EOF が来れば正常。
- `line_no`: CRLF・LF・単独CRをそれぞれ1回の改行として数える（引用符内の改行も数える）。
- **空行**（0文字の行）はレコードとして扱わず無視（物理行はカウントする）。カンマのみの行はレコード。EOF直前の末尾改行は新レコードを作らない。
- 最初のレコードが `csv.has_header=true` ならヘッダー。`false` のとき列名は `col1`,`col2`,…（列数は最初のレコードの列数）。
- **E017（構文エラー）条件**（1レコードにつき1件まで、`column`=null）: ①引用符が閉じないままEOF（以降のデータはそのフィールドに含める）②引用符で囲まれていないフィールド内の`"`（文字としてそのまま保持）③閉じ引用符の直後に区切り/改行/EOF以外の文字（その文字を値に連結して継続）。
- 先頭レコードより前のBOM以外の特殊処理はしない。NUL文字は通常の文字として保持（E009対象）。

## 3. ルールファイル `rules.json` v1
### 3.1 構造
```json
{
  "schema_version": 1,
  "name": "患者マスタ検査",
  "csv": {"has_header": true, "delimiter": ","},
  "columns": [
    {"name": "患者ID", "type": "string", "required": true,
     "max_length": 8, "pattern": "[0-9]{8}", "allowed_values": null,
     "min": null, "max": null, "min_date": null, "max_date": null,
     "date_formats": null,
     "number": {"allow_thousands_separator": false, "allow_fullwidth_digits": false},
     "checks": {"whitespace": true, "suspicious_chars": true, "mojibake": true}}
  ],
  "duplicates": [{"id": "dup1", "columns": ["患者ID"]}],
  "check_duplicate_records": true,
  "cross_rules": [
    {"id": "x1", "type": "compare", "left": "入院日", "op": "<=", "right": "退院日"},
    {"id": "x2", "type": "required_if", "column": "退院日",
     "when": {"column": "状態", "op": "==", "value": "退院"}},
    {"id": "x3", "type": "range_if", "column": "点数", "min": 0, "max": 100,
     "when": {"column": "区分", "op": "in", "values": ["A", "B"]}},
    {"id": "x4", "type": "sum_equals", "columns": ["a", "b"], "total": "c", "tolerance": 0}
  ],
  "options": {
    "null_tokens": ["NULL","null","Null","N/A","n/a","#N/A","NaN","nan","None"],
    "suspicious": {"control": true, "zero_width": true, "private_use": false},
    "outlier": {"enabled": false, "method": "iqr", "k": 1.5},
    "max_findings": 100000
  },
  "extensions": {}
}
```
### 3.2 項目定義・既定値
| パス | 型 | 既定 | 規則 |
|---|---|---|---|
| `schema_version` | int | 必須 | 1のみ受理 |
| `name` | string | "" | 最大200文字 |
| `csv.has_header` | bool | true | |
| `csv.delimiter` | 1文字string | "," | `"`・改行は不可 |
| `columns` | array | [] | 各要素。**列名は一意**。ここに無い列は「検査対象外」（構造検査E012/E013/E017と`duplicates`/`check_duplicate_records`/`cross_rules`は列定義の有無によらず動作） |
| `columns[].name` | string | 必須 | 空不可。CSVヘッダーに存在しなくてもルールとしては有効（検査時に**無視**。ヘッダー無一致は§4 `warnings`に `COLUMN_NOT_FOUND`） |
| `columns[].type` | enum | "string" | `string`,`integer`,`decimal`,`date`,`datetime` |
| `columns[].required` | bool | false | |
| `columns[].max_length` | int≥0 / null | null | |
| `columns[].pattern` | string / null | null | 正規表現（§3.4）。**全体一致**（トリム後の値全体） |
| `columns[].allowed_values` | string[] / null | null | 空配列不可。トリム後の値の完全一致 |
| `columns[].min`,`max` | number / null | null | `integer`/`decimal`のみ。`min<=max`。閉区間 |
| `columns[].min_date`,`max_date` | "yyyy-MM-dd" / null | null | `date`/`datetime`のみ。`min_date<=max_date`。日付部分で比較 |
| `columns[].date_formats` | string[] / null | null | `date`/`datetime`のみ。null時: date=`["yyyy-MM-dd"]`、datetime=`["yyyy-MM-dd HH:mm:ss"]` |
| `columns[].number.allow_thousands_separator` | bool | false | |
| `columns[].number.allow_fullwidth_digits` | bool | false | |
| `columns[].checks.whitespace` | bool | true | E010 |
| `columns[].checks.suspicious_chars` | bool | true | E009 |
| `columns[].checks.mojibake` | bool | true | E011 |
| `duplicates[]` | | [] | `id`一意(非空)、`columns`は1個以上でヘッダー名。存在しない列名はルールエラー |
| `check_duplicate_records` | bool | true | E003 |
| `cross_rules[]` | | [] | §3.3 |
| `options.null_tokens` | string[] | 上記9語 | 比較はトリム後・大文字小文字区別 |
| `options.suspicious.*` | bool | control=true, zero_width=true, private_use=false | |
| `options.outlier` | | enabled=false | **任意機能(W101)**。実装しない案は`enabled=true`で`UNSUPPORTED_OPTION`のルールエラーとする |
| `options.max_findings` | int 1〜10,000,000 | 100000 | |
| `extensions` | object | {} | 案独自の拡張。他案は無視する。内容は検証しない |

**トップレベルを含む全オブジェクトで未知キーはルールエラー**（`extensions`の内部を除く）。ルールJSONは10MiB以下、ネスト深さ20以下。
`columns[].min`/`max`を`string`/`date`型の列に指定、`min_date`を数値列に指定、`date_formats`を数値列に指定 → ルールエラー `TYPE_OPTION_MISMATCH`。

### 3.3 項間ルール（E015）
全て、**関与する全列の値が「空でなく、null_tokenでなく、型検査に合格」のレコードのみ評価**（それ以外はスキップ。型の不正は別ルールが報告）。
| type | 必須キー | 意味（違反条件） | 報告する`column` |
|---|---|---|---|
| `compare` | `left`,`op`(`<`,`<=`,`>`,`>=`,`==`,`!=`),`right` | `left op right` が**偽**。両列の型は同一(`integer`/`decimal`/`date`/`datetime`/`string`)。stringはコードポイント順 | `left` |
| `required_if` | `column`,`when` | `when`が真で`column`が空/null_token | `column` |
| `range_if` | `column`,`min`/`max`(少なくとも一方),`when` | `when`が真で`column`の数値が範囲外（`column`はinteger/decimal） | `column` |
| `sum_equals` | `columns`(2個以上),`total`,`tolerance`(≥0) | `\|sum(columns)-total\| > tolerance`。全てinteger/decimal | `total` |

`when`: `{"column":..,"op":..}` + `op`が`==`/`!=`なら`value`(string)、`in`/`not_in`なら`values`(string[]、1個以上)、`is_empty`/`not_empty`は追加キー無し。比較はトリム後文字列の完全一致。`when`の列の空判定はE001と同じ。`required_if`/`range_if`の`when`は空セルでも評価可能（`is_empty`のため）。
参照する列名がヘッダーに無い場合はルールエラーではなく、そのcross_rule全体を無視し`warnings`に`COLUMN_NOT_FOUND`。

### 3.4 正規表現ルール（RE2互換サブセット）
- 構文はRE2互換。**ルールエラー `PATTERN_UNSAFE`** とする条件（全案で同一の静的チェック。バックトラック型エンジンを使う案の安全確保も兼ねる）:
  1. 長さ>256文字。
  2. 先読み・後読み: `(?=` `(?!` `(?<=` `(?<!` を含む。
  3. 後方参照: `\1`〜`\9`、`\k<`、`(?P=` を含む。
  4. 量指定された括弧グループで、グループ内部に量指定子（`*` `+` `?` `{`）または`|`を含むもの。例: `(a+)+` `(a|b)*` `(\d+,?)*`。※「量指定子」は`\`エスケープされていない文字、文字クラス`[...]`内は除く。`(?:...)`含む。
- コンパイル不能 → `PATTERN_INVALID`。

### 3.5 ルールエラー形式
`{"path": "$.columns[2].max_length", "code": "...", "message": "日本語の説明"}`。`path`はJSONPath風。エラーコード: `RULES_TOO_LARGE`, `JSON_SYNTAX`, `UNSUPPORTED_VERSION`, `UNKNOWN_KEY`, `WRONG_TYPE`, `MISSING_KEY`, `DUPLICATE_NAME`, `INVALID_VALUE`, `TYPE_OPTION_MISMATCH`, `UNKNOWN_COLUMN`(duplicates内), `PATTERN_INVALID`, `PATTERN_UNSAFE`, `DATE_FORMAT_INVALID`, `UNSUPPORTED_OPTION`。**全ての誤りを列挙**（最初の1件で止めない）。

### 3.6 ルール省略時の既定ルールセット
`columns=[]`, `check_duplicate_records=true`, `duplicates=[]`, `cross_rules=[]`、`options`は既定。
> 規則: **`columns`が空配列のときに限り**、CSVの全列を「`type=string`、`required=false`、`checks`が全てtrue」の列として扱う（E009/E010/E011が全列に適用される）。`columns`が1個以上あるときは、**定義された列のみ**をセル検査する（E012/E013/E017/E003/`duplicates`/`cross_rules`は列定義の有無によらず動作）。

### 3.7 日付書式トークン
`yyyy`(4桁) `MM`(2桁固定) `M`(1〜2桁) `dd`(2桁固定) `d`(1〜2桁) `HH`(2桁) `mm`(2桁) `ss`(2桁)。それ以外の文字はリテラル（`-` `/` `.` `年` `月` `日` `:` ` ` `T` など）。数字トークンを連続させてよい（`yyyyMMdd`）。書式は少なくとも`yyyy`,(`M`|`MM`),(`d`|`dd`)を各1回含むこと（date）。datetimeはさらに`HH`,`mm`を含む。違反は`DATE_FORMAT_INVALID`。暦はグレゴリオ暦、年1〜9999、閏年考慮。
**既知書式（E006の判定用、固定）**: date=`yyyy-MM-dd`,`yyyy/MM/dd`,`yyyyMMdd`,`yyyy.MM.dd`,`yyyy年M月d日`／datetime=`yyyy-MM-dd HH:mm:ss`,`yyyy/MM/dd HH:mm:ss`,`yyyy-MM-dd HH:mm`,`yyyy/MM/dd HH:mm`,`yyyy-MM-ddTHH:mm:ss`。

## 4. 結果ファイル `findings.json` v1
```json
{
  "schema_version": 1,
  "tool": {"name": "csvqc-A", "version": "0.1.0"},
  "file": {"path": "sample.csv", "size_bytes": 1234, "sha256": "<hex>",
           "encoding": "utf-8", "encoding_confidence": "high"},
  "rules": {"name": "患者マスタ検査", "sha256": "<hex of rules file bytes or null>"},
  "started_at": "2026-11-18T10:00:00+09:00",
  "stats": {"records_total": 100, "records_with_findings": 3, "findings_total": 4,
            "error_rate": 0.03, "findings_by_rule": {"E001": 2, "E010": 2}, "truncated": false},
  "warnings": [{"code": "COLUMN_NOT_FOUND", "message": "..."}],
  "findings": [
    {"rule_id": "E001", "record_no": 5, "line_no": 6, "column": "患者ID", "column_index": 1,
     "value": "", "severity": "error", "message": "...", "suggestion": "..."}
  ]
}
```
- `error_rate` = `records_with_findings / records_total`（`records_total=0`なら0）。
- `records_total` = ヘッダーを除くレコード数。`records_with_findings` は構造ルール(E012/E013/E017/E003/E004)による検出レコードを含む。**ヘッダー行のE013は`record_no=0`**で、`records_with_findings`には数えない。
- `findings`の並び: `record_no`昇順→`column_index`昇順（nullは先頭）→`rule_id`昇順（辞書順）→`ルール内インスタンス（duplicates/cross_rules のid）昇順`。`max_findings`超過時は先頭から`max_findings`件のみ出力し`truncated=true`（`findings_total`は全件数）。
- `value`: セルの**デコード後の元の値**（トリム前）。レコード単位の検出は`null`。`column`/`column_index`はレコード単位なら`null`。ヘッダーE013は`column`=ヘッダー名（空ヘッダーは`colN`）。
- 時刻はISO 8601（タイムゾーン付き）。`sha256`は小文字16進。**同一入力・同一ルールなら`started_at`と`tool`を除き出力は完全に同一**（決定性）。
- UTF-8（BOMなし）、インデント自由。

## 5. 検査仕様（評価の正解を一意に決める）
### 5.1 判定用の定義
- **トリム文字集合**: U+0020, U+0009(TAB), U+3000, U+00A0。
- **空**: トリム後が空文字。**NULLトークン**: トリム後が`options.null_tokens`のいずれかに完全一致。
- **コードポイント数**でUnicode文字数を数える。

### 5.2 1セルの評価順（対象列のみ）。1ルールにつき1セル1件まで。
1. **空** → `required`なら **E001**、そうでなければ以降のチェックを全てスキップ（異常なし）。
2. **NULLトークン** → `required`なら **E001**、そうでなければ **E002**。以降をスキップ。
3. `checks.whitespace` かつ生の値がトリム文字で始まる/終わる → **E010**。以降の判定は**トリム後の値**を使う。
4. `checks.suspicious_chars` かつ（`suspicious.control`で制御文字を含む／`suspicious.zero_width`でゼロ幅文字を含む／`suspicious.private_use`で私用領域文字を含む）→ **E009**。
   - 制御文字: U+0000–U+0008, U+000B, U+000C, U+000E–U+001F, U+007F, U+0080–U+009F（TAB・LF・CRは含まない）。
   - ゼロ幅: U+200B, U+200C, U+200D, U+2060, U+FEFF（ファイル先頭のBOMは除去済みなので対象外）。
   - 私用領域: U+E000–U+F8FF。
5. `checks.mojibake` かつ（U+FFFDを含む／下記の文字集合`{縺,繧,繝,蜈,蜷,蛻,譁,荳,郢,隴}`から**2文字以上**を含む（異なる文字でも同一文字の繰り返しでも合計2以上）／正規表現`[ÃÂ][\u0080-¿]`にマッチ）→ **E011**。
6. `max_length`あり、トリム後のコードポイント数>`max_length` → **E016**。
7. 型検査（トリム後の値で）:
   - `integer`: `^[+-]?[0-9]+$` に合格しなければ **E005**。`decimal`: `^[+-]?([0-9]+(\.[0-9]+)?|\.[0-9]+)$`。`allow_fullwidth_digits`なら全角数字(U+FF10–FF19)を半角に変換してから判定。`allow_thousands_separator`なら `^[+-]?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?$` にも合格として許容（カンマ除去して数値化）。それ以外の文字（指数表記、`NaN`、`Infinity`、全角記号等）は**E005**。
   - `date`/`datetime`: (a)宣言書式（`date_formats`）のいずれかに**形式が一致し暦として妥当** → 合格。(b)そうでなく**既知書式のいずれかに形式一致かつ暦として妥当** → **E006**。(c)それ以外（形式不一致、または暦として不成立）→ **E007**。
   - `string`: 型検査なし。
8. `allowed_values`あり、トリム後の値が一覧に無い → **E014**（メッセージ「許可値以外」）。
9. `pattern`あり、トリム後の値が全体一致しない → **E014**（メッセージ「形式不一致」）。※8と9が同時に該当しても**同一セルでE014は1件のみ**。
10. 範囲検査: **型検査(7)に合格した場合のみ**。`integer`/`decimal`で`min`/`max`外 → **E008**。`date`/`datetime`で日付部分が`min_date`/`max_date`外 → **E008**。
   ※`datetime`の日付部分はE006（形式違い）でも比較対象外（宣言書式に合格した値のみ範囲検査）。

### 5.3 レコード・ファイル単位
- **E012**: データレコードのセル数≠ヘッダー（または最初のレコード）の列数 → 1件（`column`=null）。そのレコードでも位置が存在する列のセル検査は実施。
- **E013**: ヘッダー内で、2回目以降に現れる同名、および空名 → 各1件（`record_no=0`, `line_no=1`, `column`=名前または`colN`）。以降のルールが列名で参照するときは**最初に現れた列**を使う。
- **E017**: §2.2。
- **E003**: `check_duplicate_records=true`のとき、全セルの並び（セル数が異なれば異なるとみなす）が既出のデータレコードと完全一致するレコード → **2件目以降**を報告（`first_seen_record_no`付き、`column`=null）。値は比較時に正規化しない。
- **E004**: `duplicates[]`の各エントリについて、キー列の値の組（**トリム前の生の値**）が既出と一致するレコードを2件目以降報告（`column`=キー列名を`|`で連結、`first_seen_record_no`付き）。**キー列のいずれかが空（トリム後）のレコードは対象外**（初出にも数えない）。
- **E015**: §3.3。
- **W101**（任意）: `outlier.enabled`のとき、integer/decimal列の型検査合格値でIQR法（四分位は線形補間、`k`倍）の外側 → severity=`warning`。実装は任意。

### 5.4 severity
`E009`,`E011`,`W101` = `warning`、他の全ルール = `error`。

### 5.5 `message` / `suggestion` の文言（日本語固定テンプレート。評価では比較しないが全案で統一する）
| ルール | message | suggestion |
|---|---|---|
| E001 | 必須項目「{col}」が空です | 値を入力してください |
| E002 | 「{col}」に未入力を表す文字列「{value}」があります | 空欄にするか、正しい値を入力してください |
| E003 | 他の行（{first}行目のレコード）と全項目が同じ重複行です | 重複行を削除するか、内容の違いを確認してください |
| E004 | キー「{cols}」が{first}番目のレコードと重複しています | キーの値を確認し、重複を解消してください |
| E005 | 「{col}」が数値ではありません | 半角数字で入力し直してください（例: 123, 12.5） |
| E006 | 「{col}」の日付形式が指定と異なります | 指定形式（{formats}）に統一してください |
| E007 | 「{col}」が日付として成立しません | 存在する日付を指定形式で入力してください |
| E008 | 「{col}」が許容範囲({range})を外れています | 範囲内の値に修正してください |
| E009 | 「{col}」に制御文字・ゼロ幅文字などの不可視/特殊文字があります | 該当文字を削除してください |
| E010 | 「{col}」の前後に空白があります | 前後の空白を削除してください |
| E011 | 「{col}」に文字化けの疑いがあります | 元データの文字コードを確認し、正しい文字コードで読み直してください |
| E012 | 列数が{n}個で、ヘッダーの{m}個と一致しません | 区切り文字や引用符の過不足を確認してください |
| E013 | ヘッダー「{name}」が重複または空です | 列名を一意で空でないものにしてください |
| E014 | 「{col}」が許可値/形式に合いません | 許可された値または形式に修正してください |
| E015 | 項目間のルール({rule})に違反しています | 関連する項目を見直してください |
| E016 | 「{col}」が最大{max}文字を超えています（{len}文字） | 文字数を減らしてください |
| E017 | CSVの構文エラー（引用符の不整合）があります | ダブルクォーテーションの対応を確認してください |
| W101 | 「{col}」の値が他の値と大きく外れています | 入力ミスでないか確認してください |

## 6. 評価時の突合
`(rule_id, record_no, column)` の集合で比較。`message`/`suggestion`/`value`/`line_no`は突合に使わない（ただし`line_no`と`value`は別テストで検証）。

---
## 変更履歴
| 版 | 日付 | 内容 |
|---|---|---|
| v1.0 | 2026-11-18 | 初版（凍結） |
| v1.0.1 | 2026-11-18 | §3.4 後方参照の禁止対象に `(?P=` を追加 |
