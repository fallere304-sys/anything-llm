"""Android 版タチコマの外界との境界 (カーネル)。

PC 版の思考 (agent・好奇心・深掘り・記憶・学習データ・先見・自発性・自己進化) はそのまま使い、
PC 固有の部分だけを端末のものに差し替える:

    推論     Ollama (GPU)          → llama.cpp (端末の CPU・JNI)        llm.py
    入力     マイク・カメラ・ファイル → 端末の音声認識・カメラ (ML Kit)・文字入力   device.py
    出力     声と画面              → 文字だけ (チャット画面と通知)          device.py
    学習     GPU で LoRA           → 学習データを書き出して PC で学習し、アダプタを取り込んで同じ規則で採否  learner.py

Kotlin 側 (android/app) とは、Bridge (入出力) と LlamaEngine (推論) の 2 つの窓口だけでつながる。
"""
