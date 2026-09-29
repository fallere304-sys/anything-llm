"""カーネル: タチコマ自身には書き換えられない部分。

進化は「変異」と「選択」でできている。変異 (思考のコード・プロンプト・パラメータ) は
タチコマに任せるが、選択の環境 (評価・テスト・資源の上限・外界との境界) は外に置く。
生物も、自分の遺伝子は変えられても物理法則や自然選択は書き換えられない。

評価器を自分で書き換えられる進化は、評価をごまかす方向に最短で進む (報酬ハッキング)。
だからこれは安全装置であると同時に、進化が本当に「改良」になるための条件でもある。

カーネルの変更はタチコマが「提案」でき、人間が /approve したときだけ反映される。
"""

import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 選択の環境と、外界 (ネット・プロセス・OS・ハードウェア) との境界
KERNEL_PATHS = (
    "tachikoma/kernel/", "tachikoma/__init__.py",
    "tachikoma/__main__.py",
    "tachikoma/config.py",      # 資源の上限と既定値
    "tachikoma/llm.py",         # 推論サーバーとの通信
    "tachikoma/web.py",         # 外に出る検索 (個人情報フィルタ)
    "tachikoma/news.py",        # ニュースの取得と検索 (同上)
    "tachikoma/android/",       # 端末の推論・入出力・学習の採否 (Android 版の外界との境界)
    "tachikoma/scholar.py",     # 論文・政府文書の検索 (同上)
    "tachikoma/tasks.py",       # 相棒の頼みの実行 (ブログを読みに行く・学習を始める)
    "tachikoma/tools.py",       # 量子化などの外部ツール実行
    "tachikoma/tts.py", "tachikoma/audio.py", "tachikoma/camera.py", "tachikoma/sensors.py",
    "tachikoma/study.py",       # ffmpeg / yt-dlp
    "tachikoma/eyes.py",        # PDF の取得とファイル書き込み
    "tachikoma/ocr.py", "tachikoma/ui.py", "tachikoma/power.py",
    "tachikoma/eye_learner.py",
    "tachikoma/dataset.py",     # 何を学習データにするか (自分の出力を学習に混ぜない規則)
    "tachikoma/activities.py",  # 独りの時間の「伸び」の測り方 (評価そのもの)
    "tachikoma/learner.py", "tachikoma/asr_learner.py",   # 学習結果の採否 (選択)
    "tests/", "finetune/", "supervisor.py",
    "requirements", "docker-compose.yml", "docker/", "packaging/", "ui/",
)

# タチコマが自由に改良できる「思考」の部分
EVOLVABLE_PATHS = (
    "tachikoma/agent.py", "tachikoma/curiosity.py", "tachikoma/memory.py",
    "tachikoma/prompts.py", "tachikoma/persona.py", "tachikoma/epistemics.py", "tachikoma/attention.py",
    "tachikoma/text.py", "tachikoma/probes.py", "tachikoma/asr.py",
    "tachikoma/idle.py", "tachikoma/expression.py",
    "tachikoma/inquiry.py",
    "evolvable/",
)


def rel(path, root=ROOT):
    return os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")


def is_kernel(relpath):
    return any(relpath == p or relpath.startswith(p) for p in KERNEL_PATHS)


def is_evolvable(relpath):
    if is_kernel(relpath):
        return False
    return any(relpath == p or relpath.startswith(p) for p in EVOLVABLE_PATHS)


def manifest(root=ROOT):
    """カーネルのファイルのハッシュ一覧 (改変検出用)。"""
    out = {}
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "finetune_runs", "study",
                                                        "lab", "evolution", "anchors")]
        for fn in files:
            p = os.path.join(dirpath, fn)
            r = rel(p, root)
            if is_kernel(r) and not fn.endswith(".pyc"):
                with open(p, "rb") as f:
                    out[r] = hashlib.sha256(f.read()).hexdigest()
    return out
