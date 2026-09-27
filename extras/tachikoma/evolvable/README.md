# evolvable/ — タチコマが自分で書き換えてよい場所

- `params.json` … 自己進化で調整された性格・思考のパラメータ (範囲は `tachikoma/kernel/bounds.json`)
- `prompts/NAME.txt` … 自己進化で書き直された指示文 (起動時に `prompts.NAME` を置き換える)
- `plugins/` … 自己進化で作られたプラグイン (許可されたモジュールしか import できない)

ここにあるものはすべて、隔離テストと試用期間を通ったもの。`/revert N` で戻せる。
