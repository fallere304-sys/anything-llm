"""自己改変の検査。タチコマが書いた変更を、実行する前に静的に調べる。

1. 変更してよいのは進化可能な「思考」のファイルだけ (カーネル・テストは不可)
2. 思考のコードは外界に直接触れない: プロセス起動・ネットワーク・OS 操作・動的実行を禁止
   (外界に触れる必要があるときは、カーネルの狭い API を通す)
3. プラグインはさらに厳しく、許可したモジュールしか import できない
4. 構文として正しいこと
"""

import ast

from . import is_evolvable, is_kernel

FORBIDDEN_MODULES = {
    "subprocess", "socket", "ctypes", "urllib", "http", "requests", "shutil", "multiprocessing",
    "importlib", "pickle", "marshal", "builtins", "sys", "signal", "asyncio", "ssl", "ftplib",
    "smtplib", "telnetlib", "winreg", "_winapi", "pty", "tempfile", "threading",
}
# 思考のコードから import してよい内部モジュール (カーネルの実行系は不可)
FORBIDDEN_INTERNAL = {"tachikoma.kernel.evolve", "tachikoma.kernel.sandbox", "tachikoma.kernel.guard",
                      "tachikoma.kernel.runtime", "tachikoma.kernel.cpu_brain", "tachikoma.learner",
                      "tachikoma.asr_learner", "tachikoma.eye_learner", "tachikoma.config",
                      "tachikoma.kernel.foresight", "tachikoma.kernel.initiative", "tachikoma.kernel.plugins"}
# 先見の帳簿 (後で役に立ったか) の表。思考のコードが「役に立った」を水増しできないように、名前にも触れさせない
PROTECTED_TABLES = ("info_items", "foresight_kv")
# 学習結果の採否・進化の停止スイッチなど、選択の環境が使う記録の名前。思考のコードからは書けない
PROTECTED_KEYS = {"active_model", "active_asr", "active_ocr", "evolution_frozen", "evolution_last",
                  "asr_versions", "ocr_versions", "finetune_backoff_until", "asr_backoff_until", "ocr_backoff_until"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "globals", "breakpoint", "input"}
FORBIDDEN_ATTRS = {"system", "popen", "remove", "unlink", "rmdir", "removedirs", "rename", "replace",
                   "kill", "fork", "execv", "execve", "execl", "spawnl", "spawnv", "startfile", "chmod",
                   "chown", "truncate", "__subclasses__", "__globals__", "__builtins__", "__code__"}
# プラグインでは、内部に回り込む手段になる組み込み関数も使えない (実行時にも組み込み関数を絞る)
PLUGIN_FORBIDDEN_NAMES = {"open", "getattr", "setattr", "delattr", "vars", "dir", "globals", "locals", "type",
                          "object", "super", "memoryview", "help", "exit", "quit", "classmethod", "staticmethod",
                          "property", "id", "hash", "iter", "next", "callable"}
PLUGIN_ALLOWED = {"re", "math", "json", "statistics", "collections", "itertools", "functools",
                  "datetime", "time", "random", "string", "unicodedata", "tachikoma.text"}


def _module_names(node):
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:   # 相対 import → tachikoma パッケージ内
            base = "tachikoma." + base if base else "tachikoma"
        return [base] + [f"{base}.{a.name}" for a in node.names]
    return []


def check_source(relpath, source, plugin=False):
    """違反の説明のリストを返す (空なら合格)。"""
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError as e:
        return [f"{relpath}: 構文エラー {e.msg} (line {e.lineno})"]
    errors = []
    for node in ast.walk(tree):
        for name in _module_names(node):
            top = name.split(".")[0]
            if plugin:
                if not (name in PLUGIN_ALLOWED or top in PLUGIN_ALLOWED
                        or any(name.startswith(a + ".") for a in PLUGIN_ALLOWED)):
                    if not (isinstance(node, ast.ImportFrom) and node.module and
                            (node.module in PLUGIN_ALLOWED)):
                        errors.append(f"{relpath}:{node.lineno}: プラグインで import できない: {name}")
            elif top in FORBIDDEN_MODULES or name in FORBIDDEN_INTERNAL:
                errors.append(f"{relpath}:{node.lineno}: 思考のコードで使えないモジュール: {name}")
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in FORBIDDEN_CALLS:
                errors.append(f"{relpath}:{node.lineno}: 動的実行は禁止: {f.id}()")
            if isinstance(f, ast.Attribute) and f.attr in FORBIDDEN_ATTRS:
                errors.append(f"{relpath}:{node.lineno}: OS 操作は禁止: .{f.attr}()")
            if isinstance(f, ast.Name) and f.id == "open":
                mode = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if mode is not None and any(c in str(mode) for c in "wax+"):
                    errors.append(f"{relpath}:{node.lineno}: ファイルへの書き込みは禁止 (記憶は DB に)")
                elif mode is None and (len(node.args) > 1 or any(k.arg == "mode" for k in node.keywords)):
                    errors.append(f"{relpath}:{node.lineno}: open のモードは定数で書くこと")
        if not plugin and isinstance(node, ast.Constant) and node.value in PROTECTED_KEYS:
            errors.append(f"{relpath}:{node.lineno}: 選択の環境の記録には触れられない: {node.value!r}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                any(t in node.value for t in PROTECTED_TABLES):
            errors.append(f"{relpath}:{node.lineno}: 先見の帳簿には触れられない: {node.value[:40]!r}")
        if plugin and isinstance(node, ast.Import) and any(a.name.startswith("tachikoma") for a in node.names):
            errors.append(f"{relpath}:{node.lineno}: tachikoma.text は `from tachikoma.text import ...` の形でだけ使える")
        if plugin and isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append(f"{relpath}:{node.lineno}: プラグインは内部 (_ で始まる名前) に触れられない: {node.attr}")
        if plugin and isinstance(node, ast.Name) and node.id in PLUGIN_FORBIDDEN_NAMES:
            errors.append(f"{relpath}:{node.lineno}: プラグインでは使えない: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in ("__subclasses__", "__globals__", "__builtins__"):
            errors.append(f"{relpath}:{node.lineno}: 内部属性への接近は禁止: {node.attr}")
    return errors


def check_edits(edits, read_file):
    """edits: [{"file", "search", "replace"}] を検査し、(違反, 適用後の {file: source}) を返す。"""
    errors, result = [], {}
    for e in edits:
        path = (e.get("file") or "").replace("\\", "/").lstrip("./")
        if ".." in path.split("/"):
            errors.append(f"{path}: 親ディレクトリは不可")
            continue
        if is_kernel(path):
            errors.append(f"{path}: カーネルは変更できない (/propose で提案のみ可)")
            continue
        if not is_evolvable(path) or not path.endswith((".py", ".txt", ".json")):
            errors.append(f"{path}: 進化可能なファイルではない")
            continue
        src = result.get(path)
        if src is None:
            src = read_file(path)
        search, replace = e.get("search", ""), e.get("replace", "")
        if src is None:
            if search:
                errors.append(f"{path}: 存在しないファイル")
                continue
            src = ""
        if search:
            n = src.count(search)
            if n != 1:
                errors.append(f"{path}: 置換元がちょうど 1 箇所に一致しない ({n} 箇所)")
                continue
            src = src.replace(search, replace)
        else:
            if src:
                errors.append(f"{path}: 既存ファイルの丸ごと置換は不可 (search を指定)")
                continue
            src = replace
        if len(src) > 200_000:
            errors.append(f"{path}: 大きすぎる")
            continue
        result[path] = src
    for path, src in result.items():
        if path.endswith(".py"):
            errors += check_source(path, src, plugin=path.startswith("evolvable/plugins/"))
    return errors, result
