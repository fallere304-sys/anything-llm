"""Tachikoma.exe の中身 (payload.zip) を作る。Windows 上で動かす (組み込み版 Python に pip を入れるため)。

    python packaging/build_app.py --out build/payload.zip

1. python.org の組み込み版 Python 3.11 (Windows x64) を取ってきて、site-packages と本体の場所を読めるようにし、pip を入れる
2. 本体のソース (git が管理しているもの) を app/ に入れる
3. 各ファイルのハッシュと「進化してよいファイルか」を manifest.json に書く (更新のときに進化の結果を守るため)
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_VERSION = "3.11.9"
EMBED = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
GET_PIP = "https://bootstrap.pypa.io/get-pip.py"
# 本体に要らないもの (作り方・検証用の物)
SKIP = ("packaging/", "build/", "dist/", "android/")


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tachikoma-build"}), timeout=120) as r:
        return r.read()


def app_files():
    out = subprocess.run(["git", "ls-files", "-z", "."], cwd=HERE, capture_output=True, check=True).stdout
    files = [f for f in out.decode("utf-8").split("\0") if f]
    return sorted(f for f in files if not f.startswith(SKIP) and os.path.isfile(os.path.join(HERE, f)))


def build_python(stage):
    py = os.path.join(stage, "python")
    os.makedirs(py, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(fetch(EMBED))).extractall(py)
    pth = next(f for f in os.listdir(py) if f.endswith("._pth"))
    with open(os.path.join(py, pth), "w", encoding="utf-8") as f:
        # 標準ライブラリ・site-packages・本体 (..\app) を読めるようにする
        f.write(f"{pth[:-5]}.zip\n.\nLib\\site-packages\n..\\app\nimport site\n")
    with open(os.path.join(stage, "get-pip.py"), "wb") as f:
        f.write(fetch(GET_PIP))
    subprocess.run([os.path.join(py, "python.exe"), os.path.join(stage, "get-pip.py"), "--no-warn-script-location"],
                   check=True)
    os.remove(os.path.join(stage, "get-pip.py"))
    return py


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "build", "payload.zip"))
    ap.add_argument("--stage", default=os.path.join(HERE, "build", "stage"))
    ap.add_argument("--no-python", action="store_true",
                    help="本体のソースだけにする (Android 版: Python は Chaquopy が持つ)")
    args = ap.parse_args()
    sys.path.insert(0, HERE)
    from tachikoma.kernel import is_evolvable

    py = None if args.no_python else build_python(args.stage)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    version = f"{datetime.date.today():%Y.%m.%d}-{sha or 'local'}"
    manifest = {"version": version, "python": PY_VERSION, "files": {}}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _, names in os.walk(py) if py else ():
            for n in names:
                if "__pycache__" in root:
                    continue
                full = os.path.join(root, n)
                zf.write(full, "python/" + os.path.relpath(full, py).replace("\\", "/"))
        for rel in app_files():
            with open(os.path.join(HERE, rel), "rb") as f:
                data = f.read()
            zf.writestr("app/" + rel, data)
            manifest["files"][rel] = {"sha": hashlib.sha256(data).hexdigest(), "evolvable": is_evolvable(rel)}
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"{args.out}: version {version}, {len(manifest['files'])} app files, "
          f"{os.path.getsize(args.out) / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
