"""取り除いて、入れる前の姿に戻す (packaging/uninstaller.py・footprint.py)。

偽の PC (利用者のフォルダ・Ollama・レジストリ) の上で、入れる → 使う → 取り除く、を通して確かめる。
packaging/ はアプリ本体には入らないので、入れたあとのアプリの中ではこのテストは飛ばされる。"""

import json
import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "packaging"))
try:
    import footprint
    import launcher
    import uninstaller
except ImportError:  # 入れたあとのアプリには packaging/ が無い
    footprint = launcher = uninstaller = None


class FakeRegistry:
    def __init__(self):
        self.keys = {}          # path -> {name: value}

    def exists(self, path):
        return path in self.keys

    def subkeys(self, path):
        pre = path + "\\"
        return sorted({k[len(pre):].split("\\")[0] for k in self.keys if k.startswith(pre)})

    def values(self, path):
        return list(self.keys.get(path, {}))

    def set_values(self, path, values):
        self.keys.setdefault(path, {}).update(values)

    def delete_value(self, path, name):
        del self.keys[path][name]

    def delete_tree(self, path):
        for k in [k for k in self.keys if k == path or k.startswith(path + "\\")]:
            del self.keys[k]


class FakeSystem:
    windows = False

    def __init__(self):
        self.registry = FakeRegistry()
        self.later = []

    def run(self, cmd, timeout=300):
        return 127, ""

    def which(self, name):
        return None

    def processes_under(self, root):
        return []

    def kill(self, pid):
        pass

    def firewall_rules(self, root):
        return []

    def remove_firewall_rule(self, name):
        pass

    def delete_later(self, path):
        self.later.append(path)


class FakeOllama:
    def __init__(self, models):
        self.set = set(models)
        self.deleted = []

    def models(self):
        return set(self.set)

    def delete(self, name):
        self.set.discard(name)
        self.deleted.append(name)

    def close(self):
        pass


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@unittest.skipIf(uninstaller is None, "packaging/ が無い")
class UninstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.profile = os.path.join(root, "me")
        self.env = {"USERPROFILE": self.profile, "LOCALAPPDATA": os.path.join(self.profile, "AppData", "Local"),
                    "APPDATA": os.path.join(self.profile, "AppData", "Roaming"), "TEMP": os.path.join(root, "tmp")}
        os.makedirs(self.env["TEMP"])
        self.home = os.path.join(self.env["LOCALAPPDATA"], "Tachikoma")
        self.hub = footprint.hf_hub_dir(self.env)
        # 入れる前からあったもの
        self.manifest("llama3", "latest")
        touch(os.path.join(self.hub, "models--someone--mine", "x"))
        self.out = []

    def tearDown(self):
        os.chdir(HERE)
        self._tmp.cleanup()

    def manifest(self, name, tag):
        touch(os.path.join(footprint.ollama_models_dir(self.env), "manifests", "registry.ollama.ai", "library",
                           name, tag), "{}")

    def use_tachikoma(self, reg, legacy=False):
        """入れて使ったあとの姿を作る。"""
        led = footprint.Ledger(self.home)
        if not legacy:
            led.snapshot(self.env)
        touch(os.path.join(self.home, "app", "tachikoma.db"), "memory")
        touch(os.path.join(self.home, "app", "config.json"), json.dumps({"model": "gemma4:e2b"}))
        touch(os.path.join(self.home, "app", "models", "big.gguf"), "9GB")
        touch(os.path.join(self.home, "python", "python.exe"))
        touch(os.path.join(self.home, "cache", "huggingface", "hub", "models--x--y", "w"))
        led.note("ollama_models", "gemma4:e2b")
        if legacy:
            led.data.pop("created", None)
            led.save()
        self.manifest("gemma4", "e2b")
        self.manifest("tachikoma-v1", "latest")
        touch(os.path.join(self.hub, "models--kha-white--manga-ocr-base", "w"))       # 古い版はここに置いていた
        touch(footprint.start_menu_link(self.env))
        touch(os.path.join(self.env["TEMP"], "tachikoma_tts.wav"))
        reg.set_values(footprint.UNINSTALL_KEY, {"DisplayName": "Tachikoma"})
        mic = uninstaller.CONSENT + "\\microphone\\NonPackaged"
        reg.set_values(mic + "\\" + os.path.join(self.home, "python", "python.exe").replace(os.sep, "#"), {"a": 1})
        reg.set_values(mic + "\\C:#Other#app.exe", {"a": 1})
        reg.set_values(uninstaller.MUICACHE, {os.path.join(self.home, "Tachikoma.exe") + ".FriendlyAppName": "T",
                                              "C:\\Other\\app.exe.FriendlyAppName": "O"})
        return led

    def make(self, system, ollama):
        return uninstaller.Uninstaller(self.home, env=self.env, system=system, ollama=ollama, out=self.out.append)

    def test_removes_only_what_tachikoma_brought_and_verifies(self):
        system = FakeSystem()
        self.use_tachikoma(system.registry)
        ollama = FakeOllama(footprint.ollama_models(self.env))
        un = self.make(system, ollama)
        self.assertEqual(un.run(yes=True), 0, "\n".join(self.out))

        self.assertFalse(os.path.exists(self.home))
        self.assertFalse(os.path.exists(footprint.start_menu_link(self.env)))
        self.assertFalse(os.path.exists(os.path.join(self.env["TEMP"], "tachikoma_tts.wav")))
        self.assertFalse(os.path.exists(os.path.join(self.hub, "models--kha-white--manga-ocr-base")))
        self.assertEqual(sorted(ollama.deleted), ["gemma4:e2b", "tachikoma-v1:latest"])
        # 入れる前からあったものは残る
        self.assertEqual(ollama.models(), {"llama3:latest"})
        self.assertTrue(os.path.exists(os.path.join(self.hub, "models--someone--mine")))
        self.assertEqual(sorted(system.registry.keys), sorted([
            uninstaller.CONSENT + "\\microphone\\NonPackaged\\C:#Other#app.exe", uninstaller.MUICACHE]))
        self.assertEqual(list(system.registry.keys[uninstaller.MUICACHE]), ["C:\\Other\\app.exe.FriendlyAppName"])
        self.assertTrue(any("すべて取り除きました" in line for line in self.out))
        self.assertEqual(self.make(FakeSystem(), ollama).run(yes=True), 0)      # 二度目は何も残っていない
        self.assertIn("タチコマが残したものは、もう何もありません。", self.out)

    def test_dry_run_touches_nothing(self):
        system = FakeSystem()
        self.use_tachikoma(system.registry)
        ollama = FakeOllama(footprint.ollama_models(self.env))
        self.assertEqual(self.make(system, ollama).run(dry=True), 0)
        self.assertTrue(os.path.exists(os.path.join(self.home, "app", "tachikoma.db")))
        self.assertEqual(ollama.deleted, [])
        self.assertTrue(system.registry.exists(footprint.UNINSTALL_KEY))

    def test_without_a_ledger_unsure_things_are_asked(self):
        system = FakeSystem()
        self.use_tachikoma(system.registry, legacy=True)
        ollama = FakeOllama(footprint.ollama_models(self.env))
        self.assertEqual(self.make(system, ollama).run(yes=True), 0)
        self.assertEqual(ollama.deleted, ["tachikoma-v1:latest"])          # 確かなものだけ
        self.assertTrue(os.path.exists(os.path.join(self.hub, "models--kha-white--manga-ocr-base")))
        self.assertIn("gemma4:e2b", ollama.models())
        asked = []
        un = self.make(FakeSystem(), ollama)
        self.assertEqual(un.run(ask=lambda q, d: asked.append(q) or True), 0)
        self.assertNotIn("gemma4:e2b", ollama.models())
        self.assertFalse(os.path.exists(os.path.join(self.hub, "models--kha-white--manga-ocr-base")))
        self.assertTrue(any("gemma4:e2b" in q for q in asked))
        self.assertEqual(ollama.models(), {"llama3:latest"})

    def test_saying_no_keeps_everything(self):
        system = FakeSystem()
        self.use_tachikoma(system.registry)
        self.assertEqual(self.make(system, FakeOllama(set())).run(ask=lambda q, d: False), 1)
        self.assertTrue(os.path.exists(self.home))

    def test_backup_keeps_memory_but_not_models(self):
        system = FakeSystem()
        self.use_tachikoma(system.registry)
        dst = os.path.join(self._tmp.name, "backup")
        self.assertEqual(self.make(system, FakeOllama(set())).run(yes=True, backup=dst), 0)
        [name] = os.listdir(dst)
        with zipfile.ZipFile(os.path.join(dst, name)) as zf:
            names = zf.namelist()
        self.assertIn("app/tachikoma.db", names)
        self.assertIn("footprint.json", names)
        self.assertFalse(any(n.startswith("app/models/") for n in names))


@unittest.skipIf(uninstaller is None, "packaging/ が無い")
class LedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._tmp.name, "Tachikoma")

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_install_is_the_before_picture(self):
        led = footprint.Ledger(self.home)
        self.assertTrue(led.snapshot({"USERPROFILE": self._tmp.name}))
        self.assertFalse(led.snapshot({"USERPROFILE": self._tmp.name}))       # 写すのは一度だけ
        self.assertIs(footprint.Ledger(self.home).was_there("ollama_models", "gemma4:e2b"), False)
        old = footprint.Ledger(os.path.join(self._tmp.name, "other"))
        old.snapshot({"USERPROFILE": self._tmp.name}, legacy=True)
        self.assertIsNone(old.was_there("ollama_models", "gemma4:e2b"))      # 後から写した台帳は当てにしない

    def test_launcher_registers_and_keeps_caches_inside(self):
        app = launcher.App(self.home, interactive=False)
        reg = FakeRegistry()
        self.assertTrue(app.register(os.path.join(self.home, "Tachikoma.exe"), registry=reg))
        self.assertIn("--uninstall", reg.keys[footprint.UNINSTALL_KEY]["UninstallString"])
        self.assertEqual(app.ledger.created("registry"), [footprint.UNINSTALL_KEY])
        env = app.env()
        for key in ("PIP_CACHE_DIR", "HF_HOME", "TORCH_HOME"):
            self.assertTrue(env[key].startswith(self.home), key)

    def test_ollama_manifest_names(self):
        env = {"USERPROFILE": self._tmp.name}
        root = os.path.join(footprint.ollama_models_dir(env), "manifests")
        touch(os.path.join(root, "registry.ollama.ai", "library", "gemma4", "e2b"))
        touch(os.path.join(root, "registry.ollama.ai", "someone", "model", "q4"))
        touch(os.path.join(root, "hf.co", "org", "repo", "latest"))
        self.assertEqual(footprint.ollama_models(env), {"gemma4:e2b", "someone/model:q4", "hf.co/org/repo:latest"})


if __name__ == "__main__":
    unittest.main()
