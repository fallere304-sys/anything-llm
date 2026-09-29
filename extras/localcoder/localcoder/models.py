"""考える力 (GGUF モデル) の候補と取得。

ファイル名は Hugging Face の API で一覧を取り、量子化の優先順に選ぶ (分割された GGUF は全部そろえる)。
大きさ・速さは目安。速さは i7-7700・メモリ 16GB・GTX 1060 6GB くらいの PC で、SSD から読みながら動かす場合の見込み。
"""

import json
import os
import re
import urllib.parse
import urllib.request

from .server import download

API = "https://huggingface.co/api/models/"
CATALOG = [
    {"key": "standard", "label": "Qwen3-Coder 30B-A3B (標準)", "gb": 19,
     "repos": ["unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF"],
     "prefer": ["Q4_K_M", "UD-Q4_K_XL", "Q4_0", "Q3_K_M"],
     "note": "コード専用。メモリと GPU にだいたい載る。速さの目安: 数トークン/秒"},
    {"key": "large", "label": "gpt-oss 120B (大・SSD から読みながら動く)", "gb": 64,
     "repos": ["ggml-org/gpt-oss-120b-GGUF", "unsloth/gpt-oss-120b-GGUF"],
     "prefer": ["mxfp4", "MXFP4", "Q4_K_M", "F16"],
     "note": "賢さは上。1 トークンに数秒かかることもある (SSD の速さしだい)"},
    {"key": "huge", "label": "GLM-4.5-Air 106B (最大・とても遅い)", "gb": 70,
     "repos": ["unsloth/GLM-4.5-Air-GGUF"],
     "prefer": ["Q4_K_M", "UD-Q4_K_XL", "Q3_K_M", "UD-Q3_K_XL"],
     "note": "道具を使うのが得意。1 回の返事に数十分かかることもある"},
    {"key": "tiny", "label": "Qwen2.5-Coder 1.5B (試しに動かすだけ)", "gb": 1.2,
     "repos": ["Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"], "prefer": ["q4_k_m", "q5_k_m", "q8_0"],
     "note": "とても速いが、簡単なことしかできない (動作確認用)"},
]
_SPLIT = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)


def by_key(key):
    return next((m for m in CATALOG if m["key"] == key), None)


def pick_files(siblings, prefer):
    """優先する量子化の GGUF を選ぶ。分割されていれば、同じ組の全部を順に返す。"""
    ggufs = [s for s in siblings if s.lower().endswith(".gguf") and "mmproj" not in s.lower()]
    for q in prefer:
        hits = [s for s in ggufs if q.lower() in s.lower()]
        if not hits:
            continue
        first = sorted(hits)[0]
        m = _SPLIT.search(first)
        if not m:
            return [first]
        stem = first[:m.start()]
        return sorted(s for s in hits if s.startswith(stem) and _SPLIT.search(s))
    return []


def siblings(repo, opener=urllib.request.urlopen):
    req = urllib.request.Request(API + repo, headers={"User-Agent": "LocalCoder"})
    with opener(req, timeout=60) as r:
        return [s["rfilename"] for s in json.loads(r.read().decode("utf-8")).get("siblings", [])]


def fetch(entry, models_dir, say=print, opener=urllib.request.urlopen):
    """候補のモデルをそろえる。最初のファイルのパス (llama-server に渡すもの) を返す。"""
    last = None
    for repo in entry["repos"]:
        try:
            files = pick_files(siblings(repo, opener), entry["prefer"])
        except (OSError, ValueError) as e:
            last = e
            continue
        if not files:
            continue
        dst_dir = os.path.join(models_dir, repo.replace("/", "__"))
        os.makedirs(dst_dir, exist_ok=True)
        paths = []
        for rel in files:
            dst = os.path.join(dst_dir, os.path.basename(rel))
            if not os.path.exists(dst):
                url = f"https://huggingface.co/{repo}/resolve/main/{urllib.parse.quote(rel)}"
                say(f"{repo} の {os.path.basename(rel)} を取得します")
                download(url, dst, say, opener)
            paths.append(dst)
        return paths[0]
    raise OSError(f"モデルを取得できませんでした: {entry['label']} ({last or 'ファイルが見つからない'})")


def local_models(models_dir):
    """手元にある GGUF (分割なら最初のものだけ)。"""
    out = []
    for d, _, files in os.walk(models_dir):
        for f in sorted(files):
            if f.lower().endswith(".gguf") and "mmproj" not in f.lower():
                m = _SPLIT.search(f)
                if m and m.group(1) != "00001":
                    continue
                out.append(os.path.join(d, f))
    return out
