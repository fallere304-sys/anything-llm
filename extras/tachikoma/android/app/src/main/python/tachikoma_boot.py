"""Android 版の入口 (Chaquopy が読み込む唯一のモジュール)。

APK に同梱したタチコマ本体 (payload.zip) を、書き込める場所 (アプリの files/home) に展開してから起動する。
自己進化は home のソースを書き換えるので、アプリを更新しても進化の結果は残す (Windows 版の Tachikoma.exe と同じ規則):
    - 進化してよいファイルがここで書き換わっていたら残し、同梱の新しい版は *.new として横に置く
    - カーネルのファイルは更新する (書き換わっていたら *.local-backup に残す)
    - 記憶・設定・アダプタ・育ったプラグインには触れない
"""

import hashlib
import json
import os
import shutil
import sys
import zipfile


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install(payload, home):
    """payload.zip を home に展開・更新する。(更新したか, 残した進化の結果) を返す。"""
    os.makedirs(home, exist_ok=True)
    mpath = os.path.join(home, ".manifest.json")
    try:
        with open(mpath, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    with zipfile.ZipFile(payload) as zf:
        new = json.loads(zf.read("manifest.json"))
        if old.get("version") == new["version"]:
            return False, []
        kept = []
        for rel, meta in new["files"].items():
            dst = os.path.join(home, *rel.split("/"))
            data = zf.read("app/" + rel)
            if os.path.exists(dst):
                cur = _sha(dst)
                if cur == meta["sha"]:
                    continue
                old_sha = old.get("files", {}).get(rel, {}).get("sha")
                changed_here = old_sha is None or cur != old_sha
                if meta["evolvable"] and changed_here:
                    with open(dst + ".new", "wb") as f:
                        f.write(data)
                    kept.append(rel)
                    continue
                if changed_here:
                    shutil.copy2(dst, dst + ".local-backup")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
        for rel, meta in old.get("files", {}).items():
            dst = os.path.join(home, *rel.split("/"))
            if rel not in new["files"] and os.path.exists(dst) and _sha(dst) == meta["sha"]:
                os.remove(dst)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False)
    return True, kept


def prepare(payload, home):
    updated, kept = install(payload, home)
    if home not in sys.path:
        sys.path.insert(0, home)
    os.chdir(home)
    try:
        import certifi              # 端末の Python に HTTPS の証明書の場所を教える
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    return updated, kept


def start(bridge, engine, payload, home, model_name):
    """Kotlin の常駐サービスから (別スレッドで) 呼ばれる。止めるまで戻らない。"""
    updated, kept = prepare(payload, home)
    if kept:
        bridge.log("自己進化で書き換わったファイルは残しました: " + ", ".join(kept))
    from tachikoma.android.main import run
    run(bridge, engine, home, model_name)
