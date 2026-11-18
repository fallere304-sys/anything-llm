# ARCHITECTURE_C.md — C案「性能・効率」（Rust ＋ egui）

## 1. コンセプト
「100万行を最小のメモリで、最短時間で、結果が毎回完全に同じ」。バイト列のまま処理し、割り当て（アロケーション）を最小化、パイプライン並列でCPUコアを使い切る。

## 2. 設計思想
- **ゼロコピー志向**: 読み込みバッファ上のバイトスライスをパースし、必要になるまで`String`化しない。ASCIIのみのセルは検査をバイト単位で行う。
- **パイプライン並列**: ①リーダ/デコーダ/パーサ(1スレッド、レコードをバッチ化) → ②チェックワーカ(Nスレッド、バッチ単位で検査、状態を持たないルール) → ③順序保証マージ（`record_no`順に再整列）。**状態を持つ検査（E003/E004重複）は③の直列段で実行**（ハッシュ集合）。
- **決定性**: 並列度を変えても`findings.json`は完全一致（マージ段で整列）。
- **メモリ上限**: `--max-memory-mb`（既定2048）。バッチ数を制限しバックプレッシャをかける（有界チャネル）。重複検出ハッシュは128bit(xxh3)のみ保持（衝突時は誤検出を避けるため**既出レコードの再比較はせず、ハッシュ一致=重複**と割り切る。衝突確率の評価をドキュメント化）。
- ファイルは`std::fs::File`を**読み取り専用**で開く。mmapは使わない（ロック/部分読込の安全性、32bit/大ファイルの不確実性回避。[合理的推定] 順次読込で十分高速）。

## 3. 技術選定（固定・GPTの知識時点）
| 用途 | クレート/版 |
|---|---|
| 言語 | Rust stable（edition 2021）。ワークスペース構成 |
| 文字コード | `encoding_rs` 0.8（`SHIFT_JIS`はWindows-31J互換。置換でU+FFFD） |
| JSON | `serde` 1 / `serde_json` 1 |
| ハッシュ | `sha2` 0.10、`xxhash-rust` 0.8（feature `xxh3`） |
| 正規表現 | `regex` 1（線形時間） |
| Excel | `rust_xlsxwriter` 0.6x系（正確な版はC-07の前にコンテナでビルド確認して固定） |
| GUI | `eframe`/`egui` 0.28、`rfd` 0.14（ファイルダイアログ） |
| 履歴 | `rusqlite` 0.31（feature `bundled`） |
| 並列 | 標準`std::thread` ＋ `std::sync::mpsc::sync_channel`（有界）。rayon不使用 |
| CLI引数 | 手書き（clap不使用でAPI取り違えを避ける） |

## 4. ワークスペース構成
```
Project/C/
  Cargo.toml                       # workspace: [crates/core, crates/cli, crates/gui]
  crates/core/                     # 検査コア。GUI/OS依存なし → Linuxでcargo test
    src/lib.rs, model.rs           # C-01
    src/pattern.rs                  # C-01
    src/rules.rs                   # C-02 (rules.json検証)
    src/sniff.rs, parser.rs        # C-03 (バイト列パーサ。&[u8]セル、行番号)
    src/check/{cell,typed,record}.rs # C-04〜C-06 (バイト/文字列両対応)
    src/pipeline.rs                # C-07/C-14 (並列パイプライン、重複、集計、キャンセル)
    src/report/*.rs                # C-08
  crates/cli/                      # C-09 (--headless, --bench)
  crates/gui/                      # C-11, C-12 (egui)
  crates/core/benches or src/bin/bench.rs # C-14
```

## 5. 設計上の要点
- `Cell<'a> = &'a [u8]`（UTF-8化はセル単位で遅延。デコード済みバッファ上のスライスを使うため、**文字コード変換はバッチ単位で`encoding_rs`のストリーミングデコーダでUTF-8に変換してからパース**）。
- **チェックはトレイトではなく静的ディスパッチ**（列ごとに「有効なチェックのビットマスク」を前計算）。
- 検出の保持: ワーカごとに`Vec<Finding>`を作りバッチ単位で返す。`max_findings`超過後は検出本体を捨てて**件数のみ加算**。
- 進捗: `AtomicU64`で処理済みバイト数を共有、GUIは定期ポーリング。キャンセルは`AtomicBool`。

## 6. メリット / デメリット / リスク
- メリット: 速度・メモリで最良の見込み、単一静的EXE、依存DLL不要（MSVCランタイムは静的リンク設定）。
- デメリット: GUI検証が困難（eframeの実表示をコンテナで確認できない）、Rustのコンパイルエラー反復が多く、GPT-OSSのやり取り回数が増える可能性が高い。
- リスク: eframeのAPI変更（0.28に固定）、日本語フォント（egui既定は日本語不可 → `Yu Gothic`/`Meiryo`をWindowsのフォントファイルから実行時に読み込む実装が必要。**同梱不可の場合のフォールバック表示崩れ**）、Windowsでのクロスビルド不要（CIでMSVC）。
- 緩和: coreを別crateにし、GUI crateは`cargo check`で型検証。日本語フォントはC-10で`C:\Windows\Fonts\YuGothM.ttc`等を探索して読み込み。

## 7. 独自機能（C-14）
パイプライン並列・メモリ上限・`--bench`（入力を生成せず与えられたCSVを検査し、スループット/ピークRSS/各段の時間を出力）。

## 8. 検証計画
`cargo test`（コア）、共通コーパス、100万行でRSS/時間、並列度1/2/4/8で出力が一致することを自動テスト化。GUIは`cargo check`＋CIビルド＋ユーザー実機。

---
## 変更履歴
| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 | 2026-11-18 | 初版 |
