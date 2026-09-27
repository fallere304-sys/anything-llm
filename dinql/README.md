# DiNQL 褥瘡ケアの取組み 4・5・8・9・10 集計ブック

- `DiNQL_褥瘡ケア集計_ver4.1.ods` … 本体（LibreOffice 5.4 想定・関数のみ・マクロなし）
- `source/DiNQL__ver.3.0.ods` … 再構成元（ver3.0）
- `tools/build.py` … ver3.0 から本体を再生成するスクリプト（LibreOffice + Python UNO）
- `tools/testrun.py` / `tools/perftest.py` … 判定ルールの検証用テストデータ投入と計算時間計測

再生成: `python3 tools/build.py source/DiNQL__ver.3.0.ods DiNQL_褥瘡ケア集計_ver4.1.ods`（tools/ で実行）
