"""Tachikoma.exe の入口 (packaging/launcher.py): 入れる・更新する (進化の結果と記憶を守る)。

packaging/ はアプリ本体には入らないので、入れたあとのアプリ・隔離環境の中ではこのテストは飛ばされる。"""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "packaging"))
try:
    import launcher
except ImportError:  # 入れたあとのアプリには packaging/ が無い
    launcher = None


def payload(files, version, python="3.11.9", evolvable=()):
    buf = io.BytesIO()
    man = {"version": version, "python": python, "files": {}}
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("python/python.exe", b"stub")
        for rel, text in files.items():
            data = text.encode("utf-8")
            zf.writestr("app/" + rel, data)
            man["files"][rel] = {"sha": hashlib.sha256(data).hexdigest(), "evolvable": rel in evolvable}
        zf.writestr("manifest.json", json.dumps(man))
    buf.seek(0)
    return buf


@unittest.skipIf(launcher is None, "packaging/ が無い")
class LauncherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._tmp.name, "Tachikoma")

    def tearDown(self):
        self._tmp.cleanup()

    def read(self, rel):
        with open(os.path.join(self.home, "app", *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def test_install_then_update_keeps_evolution_and_memory(self):
        v1 = {"tachikoma/curiosity.py": "v1-curiosity", "tachikoma/kernel/guard.py": "v1-guard",
              "tachikoma/old.py": "old", "config.example.json": json.dumps({"model": "gemma4:e2b", "web": True})}
        app = launcher.App(self.home, interactive=False)
        self.assertTrue(app.install(payload(v1, "1", evolvable={"tachikoma/curiosity.py", "tachikoma/old.py"})))
        app.ensure_config()
        cfg = app._load(app.cfg_path, {})
        self.assertEqual((cfg["web"], cfg["voice"], cfg["evolution_enabled"]), (True, False, False))
        self.assertFalse(app.install(payload(v1, "1")))          # 同じ版なら何もしない

        # 使っているうちに: 自己進化が思考を書き換え、記憶が溜まり、プラグインが育つ
        with open(os.path.join(self.home, "app", "tachikoma", "curiosity.py"), "w", encoding="utf-8") as f:
            f.write("evolved-curiosity")
        for rel, text in (("tachikoma.db", "memories"), ("evolvable/plugins/grown.py", "def on_tick(api):\n    pass\n")):
            p = os.path.join(self.home, "app", *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)

        v2 = {"tachikoma/curiosity.py": "v2-curiosity", "tachikoma/kernel/guard.py": "v2-guard",
              "config.example.json": v1["config.example.json"]}
        app = launcher.App(self.home, interactive=False)
        self.assertTrue(app.install(payload(v2, "2", evolvable={"tachikoma/curiosity.py"})))
        self.assertEqual(self.read("tachikoma/curiosity.py"), "evolved-curiosity")   # 進化の結果は残る
        self.assertEqual(self.read("tachikoma/curiosity.py.new"), "v2-curiosity")    # 新しい版は横に置く
        self.assertEqual(self.read("tachikoma/kernel/guard.py"), "v2-guard")         # カーネルは更新する
        self.assertFalse(os.path.exists(os.path.join(self.home, "app", "tachikoma", "old.py")))
        self.assertEqual(self.read("tachikoma.db"), "memories")                       # 記憶に触れない
        self.assertTrue(os.path.exists(os.path.join(self.home, "app", "evolvable", "plugins", "grown.py")))
        self.assertEqual(app._load(app.cfg_path, {})["model"], "gemma4:e2b")

    def test_new_python_means_features_are_reinstalled(self):
        app = launcher.App(self.home, interactive=False)
        app.install(payload({"a.py": "a"}, "1"))
        app.state.update(features=["voice"], features_installed=["voice"])
        app.install(payload({"a.py": "a"}, "2", python="3.11.10"))
        self.assertEqual(app.state["features_installed"], [])       # 次の起動で入れ直す
        self.assertEqual(app.state["features"], ["voice"])

    def test_non_interactive_setup_asks_nothing(self):
        app = launcher.App(self.home, interactive=False)
        app.install(payload({"a.py": "a"}, "1"))
        app.choose_features()
        self.assertEqual(app.state["features"], [])
        self.assertFalse(app.ollama.__self__.interactive)


if __name__ == "__main__":
    unittest.main()
