"""USB オールインワン: ネットにつながっていない PC にも、USB (や外付けドライブ) だけで導入できる形。

作る (ネットにつながる PC で 1 回):
    LocalCoderCLI.exe --make-bundle E:\\LocalCoder --models standard,small
    → E:\\LocalCoder\\ に LocalCoder.exe・LocalCoderCLI.exe・LocalCoderUninstall.exe・非常停止.bat・
      はじめにお読みください.txt と bundle\\ を置く。bundle\\ の中身:
        llama\\<cuda|vulkan|cpu>\\   llama.cpp (広げたもの。CUDA 版は CUDA の実行部品つき)
        models\\<…>\\*.gguf          選んだモデル
        python\\python-3.12.10-amd64.exe   python.org の Python (オフラインで入る完全版)
        wheels\\*.whl                PyInstaller と、それが要る部品 (Windows・Python 3.12 用)
        vcruntime\\*.dll             Visual C++ の実行部品 (まっさらな PC で llama.cpp が動くように)
        manifest.json               中身の一覧
使う (オフラインの PC で):
    E:\\LocalCoder\\LocalCoder.exe を開く → 置き場所 (PC の SSD) を選ぶ → bundle\\ から写して導入する。
    ダウンロードは一切しない (しようとしたら、その場で止めて知らせる)。
"""

import json
import os
import re
import shutil
import sys
import time
import urllib.request

from . import models, server, toolchain

MANIFEST = "manifest.json"
VC_DLLS = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll",
           "vcomp140.dll", "concrt140.dll"]
EXES = ["LocalCoder.exe", "LocalCoderCLI.exe", "LocalCoderUninstall.exe"]
PYPI = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION = "https://pypi.org/pypi/{name}/{version}/json"
TARGET = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64",
          "implementation_name": "cpython", "platform_python_implementation": "CPython",
          "python_version": ".".join(toolchain.PY_VERSION.split(".")[:2]), "python_full_version": toolchain.PY_VERSION,
          "extra": ""}
WHEEL_TAGS = ("py3-none-any", "py2.py3-none-any", "py3-none-win_amd64", "cp312-cp312-win_amd64", "cp312-abi3-win_amd64",
              "cp37-abi3-win_amd64", "cp38-abi3-win_amd64", "cp39-abi3-win_amd64")


# ---------------------------------------------------------------- 見つける・読む
def find(explicit=None):
    """使える bundle\\ のパス (manifest.json があるもの)。無ければ None。"""
    cands = [explicit, os.environ.get("LOCALCODER_BUNDLE")]
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "bundle"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, MANIFEST)):
            return os.path.abspath(c)
    return None


def manifest(b):
    with open(os.path.join(b, MANIFEST), encoding="utf-8") as f:
        return json.load(f)


def model_keys(b):
    """bundle に入っているモデルの key (カタログの順)。"""
    have = manifest(b).get("models", {})
    return [m["key"] for m in models.CATALOG if m["key"] in have]


def kinds(b):
    return [k for k in server.KINDS if server.find_server(os.path.join(b, "llama", k))]


# ---------------------------------------------------------------- 写す (オフラインの導入)
def copy_file(src, dst, say=print):
    """大きなファイルを、進み具合を見せながら写す (途中で切れたら次は頭から)。"""
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    total, done, last = os.path.getsize(src), 0, 0.0
    tmp = dst + ".part"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while True:
            chunk = fi.read(1 << 23)
            if not chunk:
                break
            fo.write(chunk)
            done += len(chunk)
            if time.time() - last > 1 and total > 50 << 20:
                say(f"\r  {os.path.basename(dst)}: {done / 2**30:.2f} / {total / 2**30:.2f} GB を写しました", end="")
                last = time.time()
    os.replace(tmp, dst)
    if total > 50 << 20:
        say(f"\r  {os.path.basename(dst)}: 写しました ({total / 2**30:.2f} GB)          ")


def install_llama(b, home, wanted, say=print):
    """bundle の llama.cpp を home/llama/<kind> に写す。写せた組の名前のリスト。"""
    ready = []
    for kind in wanted:
        src = os.path.join(b, "llama", kind)
        dst = os.path.join(home, "llama", kind)
        if not server.find_server(src):
            continue
        if not server.find_server(dst):
            say(f"llama.cpp ({kind} 版) を USB から写します")
            shutil.copytree(src, dst, dirs_exist_ok=True)
        vc = os.path.join(b, "vcruntime")
        exe_dir = os.path.dirname(server.find_server(dst))
        for name in (os.listdir(vc) if os.path.isdir(vc) else []):
            if name.lower().endswith(".dll") and not os.path.exists(os.path.join(exe_dir, name)):
                shutil.copy2(os.path.join(vc, name), os.path.join(exe_dir, name))    # まっさらな PC 用 (その場に置く)
        ready.append(kind)
    return ready


def install_model(b, home, key, say=print):
    """bundle のモデルを home/models に写す。llama-server に渡す最初のファイルのパスを返す。"""
    files = manifest(b).get("models", {}).get(key)
    if not files:
        raise OSError(f"USB の中にモデル「{key}」がありません (入っているもの: {', '.join(model_keys(b)) or 'なし'})")
    paths = []
    for rel in files:
        src = os.path.join(b, "models", rel)
        dst = os.path.join(home, "models", rel)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            say(f"モデル {os.path.basename(rel)} を USB から写します")
        copy_file(src, dst, say)
        paths.append(dst)
    return paths[0]


def toolchain_sources(b):
    m = manifest(b)
    inst = os.path.join(b, "python", m["python"]) if m.get("python") else None
    wheels = os.path.join(b, "wheels")
    return (inst if inst and os.path.exists(inst) else None), (wheels if os.path.isdir(wheels) else None)


# ---------------------------------------------------------------- 作る (ネットのある PC で)
def _version_key(v):
    if not re.fullmatch(r"\d+(\.\d+)*(\.post\d+)?", v):
        return None                             # 試験版 (a1・rc など) は使わない
    nums = re.findall(r"\d+", v.replace(".post", ".0."))
    return tuple(int(x) for x in nums)


def _satisfies(version, spec):
    key = _version_key(version)
    if key is None:
        return False
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        m = re.match(r"(~=|==|!=|>=|<=|>|<)\s*([\w.*]+)", part)
        if not m:
            continue
        op, want = m.groups()
        if want.endswith(".*"):
            prefix = want[:-2]
            same = version == prefix or version.startswith(prefix + ".")
            if (op == "==" and not same) or (op == "!=" and same):
                return False
            continue
        w = _version_key(want) or ()
        n = max(len(key), len(w))
        a, c = key + (0,) * (n - len(key)), w + (0,) * (n - len(w))
        ok = {"==": a == c, "!=": a != c, ">=": a >= c, "<=": a <= c, ">": a > c, "<": a < c,
              "~=": a >= c and a[:max(1, len(w) - 1)] == c[:max(1, len(w) - 1)]}[op]
        if not ok:
            return False
    return True


def _marker(marker):
    """依存の条件 (sys_platform == "win32" など) を、Windows・Python 3.12 で読む。"""
    if not marker:
        return True

    def one(m):
        var, op, val = m.group(1), m.group(2), m.group(3)
        have = TARGET.get(var, "")
        if var in ("python_version", "python_full_version") and op not in ("in", "not in"):
            return str(_satisfies(have, f"{op}{val}"))
        return str({"==": have == val, "!=": have != val, "in": have in val, "not in": have not in val}.get(op, False))
    expr = re.sub(r"""(\w+)\s*(==|!=|>=|<=|>|<|~=|not in|in)\s*["']([^"']*)["']""", one, marker)
    # 残ったのが True/False/and/or/not と括弧だけのときに限り、式として読む (それ以外は「当てはまらない」)
    if not re.fullmatch(r"[\sA-Za-z()]*", expr) or \
            any(w not in ("True", "False", "and", "or", "not") for w in re.findall(r"[A-Za-z]+", expr)):
        return False
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))   # noqa: S307
    except SyntaxError:
        return False


def _get_json(url, opener):
    with opener(urllib.request.Request(url, headers={"User-Agent": "LocalCoder"}), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def resolve_wheels(roots, opener=urllib.request.urlopen):
    """PyPI から、Windows・Python 3.12 で入る wheel の組を選ぶ。[(名前, 版, ファイル名, URL)]。

    pip が無い PC でも USB を作れるように、要るものを PyPI の JSON から自分で辿る (戻って選び直しはしない)。"""
    want, chosen, order = {_norm(r): "" for r in roots}, {}, list(roots)
    while order:
        name = _norm(order.pop(0))
        if name in chosen:
            continue
        info = _get_json(PYPI.format(name=name), opener)
        spec = want.get(name, "")
        for v in sorted(info["releases"], key=lambda x: _version_key(x) or (), reverse=True):
            if not _satisfies(v, spec):
                continue
            files = [f for f in info["releases"][v] if f["filename"].endswith(".whl") and not f.get("yanked")
                     and any(f["filename"].endswith(t + ".whl") for t in WHEEL_TAGS)]
            if files:
                f = files[0]
                chosen[name] = (info["info"]["name"], v, f["filename"], f["url"])
                break
        else:
            raise OSError(f"{name} ({spec or '最新'}) の Windows 用 wheel が PyPI に見つかりません")
        detail = _get_json(PYPI_VERSION.format(name=name, version=chosen[name][1]), opener)
        for req in detail["info"].get("requires_dist") or []:
            body, _, marker = req.partition(";")
            m = re.match(r"\s*([A-Za-z0-9_.-]+)\s*(?:\[[^\]]*\])?\s*\(?([^)]*)\)?", body)
            if not m or not _marker(marker.strip()):
                continue
            dep, dspec = _norm(m.group(1)), m.group(2).strip()
            if dep in chosen:
                if dspec and not _satisfies(chosen[dep][1], dspec):
                    raise OSError(f"{dep} {chosen[dep][1]} が {name} の条件 {dspec} に合いません")
                continue
            want[dep] = ",".join(x for x in (want.get(dep, ""), dspec) if x)
            order.append(dep)
    return list(chosen.values())


def _write_text(path, text):
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)


README = """LocalCoder (USB オールインワン版)
==================================

この USB だけで、インターネットにつながっていない PC にも LocalCoder を入れて使えます。

■ 入れ方
1. この USB のフォルダにある「LocalCoder.exe」をダブルクリックする
2. 最初の画面で、モデルを置く場所 (PC の SSD がおすすめ) を選ぶ
   → USB から PC へモデルなどを写します (大きいモデルは数十分かかります)
3. 「準備できました」と出たら、下の欄に日本語で頼む

■ 入っているもの
{contents}

■ 困ったとき
- 画面が固まった・止まらない: 「非常停止.bat」をダブルクリック
  (記録した LocalCoder とモデルだけを止めます。ほかのプログラムには触れません)
- 取り除いて元に戻す: 「LocalCoderUninstall.exe」
- コマンドプロンプトで使う: 「LocalCoderCLI.exe」

作成: {created}
"""


def make(dest, keys, kinds_=server.KINDS, say=print, opener=urllib.request.urlopen, exe_dir=None):
    """USB オールインワンを dest に作る (ネットが要る)。作った bundle\\ のパスを返す。"""
    dest = os.path.abspath(dest)
    b = os.path.join(dest, "bundle")
    os.makedirs(b, exist_ok=True)
    m = {"version": 1, "created": time.strftime("%Y-%m-%d %H:%M"), "models": {}, "python": None, "wheels": [],
         "vcruntime": []}
    # 1. LocalCoder 自身
    exe_dir = exe_dir or (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else None)
    for name in EXES:
        src = os.path.join(exe_dir, name) if exe_dir else None
        if src and os.path.exists(src) and os.path.normcase(os.path.dirname(src)) != os.path.normcase(dest):
            shutil.copy2(src, os.path.join(dest, name))
    # 2. llama.cpp (使える組を全部: どの PC でも GPU を試せるように)
    got = server.install(b, list(kinds_), say, opener)
    if not got:
        raise OSError("llama.cpp を 1 つも用意できませんでした")
    m["kinds"] = got
    # 3. モデル
    for key in keys:
        entry = models.by_key(key)
        if entry is None:
            raise OSError(f"モデル「{key}」はありません ({', '.join(x['key'] for x in models.CATALOG)})")
        first = models.fetch(entry, os.path.join(b, "models"), say, opener)
        folder = os.path.dirname(first)
        stem = re.sub(r"-\d{5}-of-\d{5}\.gguf$", "", os.path.basename(first), flags=re.I)
        parts = sorted(f for f in os.listdir(folder) if f.startswith(stem) and f.lower().endswith(".gguf"))
        m["models"][key] = [os.path.relpath(os.path.join(folder, f), os.path.join(b, "models")).replace("\\", "/")
                            for f in parts]
    # 4. Python (.exe にする道具) と PyInstaller の wheel
    os.makedirs(os.path.join(b, "python"), exist_ok=True)
    inst = os.path.basename(toolchain.INSTALLER)
    if not os.path.exists(os.path.join(b, "python", inst)):
        say(f"Python {toolchain.PY_VERSION} の導入器を取得します")
        server.download(toolchain.INSTALLER, os.path.join(b, "python", inst), say, opener)
    m["python"] = inst
    os.makedirs(os.path.join(b, "wheels"), exist_ok=True)
    say("PyInstaller と、それが要る部品を選びます (Windows・Python 3.12 用)")
    for name, version, filename, url in resolve_wheels(["pyinstaller"], opener):
        dst = os.path.join(b, "wheels", filename)
        if not os.path.exists(dst):
            server.download(url, dst, lambda *a, **k: None, opener)
        m["wheels"].append(filename)
        say(f"  {name} {version}")
    # 5. Visual C++ の実行部品 (この PC にあるものを写す。まっさらな PC で llama.cpp を動かすため)
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    os.makedirs(os.path.join(b, "vcruntime"), exist_ok=True)
    for dll in VC_DLLS:
        if os.path.exists(os.path.join(sysdir, dll)):
            shutil.copy2(os.path.join(sysdir, dll), os.path.join(b, "vcruntime", dll))
            m["vcruntime"].append(dll)
    with open(os.path.join(b, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    # 6. 使う人のための入口
    _write_text(os.path.join(dest, "非常停止.bat"),
                '@echo off\r\nchcp 65001 >nul\r\n"%~dp0LocalCoderCLI.exe" --force-stop\r\npause\r\n')
    labels = [f"- モデル: {models.by_key(k)['label']}" for k in keys]
    labels += [f"- llama.cpp: {', '.join(got)} 版", f"- Python {toolchain.PY_VERSION} と PyInstaller (.exe にする道具)"]
    _write_text(os.path.join(dest, "はじめにお読みください.txt"),
                README.format(contents="\n".join(labels), created=m["created"]))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(dest) for f in fs)
    say(f"\nUSB オールインワンを作りました: {dest}  (合計 {size / 2**30:.1f} GB)")
    return b
