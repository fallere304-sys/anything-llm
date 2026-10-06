"""アンインストーラの対象列挙・削除・安全確認のテスト（実ファイルは一時ディレクトリ内に作る）。"""
import json
import tempfile

import pytest

from app import config
from app import uninstall as un


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    local = tmp_path / "local"
    temp = tmp_path / "temp"
    for d in (home, local, temp):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))

    # 本体が作るもの
    appdata = local / config.APP_NAME
    (appdata / "models" / "whisper-medium").mkdir(parents=True)
    (appdata / "models" / "whisper-medium" / "model.bin").write_bytes(b"x" * 1000)
    (appdata / "hf_home" / "xet").mkdir(parents=True)
    (appdata / "config.json").write_text("{}")
    sessions = home / "Documents" / config.APP_NAME / "20260101_000000"
    sessions.mkdir(parents=True)
    (sessions / "mic1.wav").write_bytes(b"RIFF")

    # exe 群: アンインストーラと同じフォルダの本体 + 別フォルダに移動した本体
    dist = tmp_path / "Downloads"
    dist.mkdir()
    me = dist / un.UNINSTALL_EXE
    me.write_bytes(b"MZ")
    (dist / un.MAIN_EXE).write_bytes(b"MZ")
    moved = tmp_path / "Tools" / un.MAIN_EXE
    moved.parent.mkdir()
    moved.write_bytes(b"MZ")
    victim = tmp_path / "important.exe"  # 改ざんされた install_info に書かれた無関係ファイル
    victim.write_bytes(b"MZ")
    (appdata / "install_info.json").write_text(
        json.dumps({"exe_paths": [str(moved), str(victim), str(tmp_path / "gone" / un.MAIN_EXE)]})
    )

    # 一時展開フォルダ: 本体のもの / 他の PyInstaller アプリのもの
    ours = temp / "_MEI12345"
    (ours / "faster_whisper").mkdir(parents=True)
    (ours / "llama_cpp").mkdir()
    other = temp / "_MEI99999"
    (other / "PyQt5").mkdir(parents=True)

    return {
        "me": me, "dist": dist, "moved": moved, "victim": victim, "appdata": appdata,
        "sessions_root": home / "Documents" / config.APP_NAME, "ours": ours, "other": other,
    }


def test_find_targets_lists_everything_the_app_created(env):
    ts = {str(t.path): t for t in un.find_targets(env["me"])}
    assert str((env["dist"] / un.MAIN_EXE).resolve()) in ts
    assert str(env["moved"].resolve()) in ts
    assert str(env["victim"].resolve()) not in ts  # 名前が違う exe は対象外
    assert str(env["appdata"]) in ts and ts[str(env["appdata"])].default
    assert str(env["ours"]) in ts
    assert str(env["other"]) not in ts  # 他アプリの一時フォルダは対象外
    s = ts[str(env["sessions_root"])]
    assert s.exists and s.default is False  # ユーザーデータは既定で残す
    assert ts[str(env["me"])].key == "self"


def test_delete_defaults_removes_app_but_keeps_user_data(env):
    targets = un.find_targets(env["me"])
    for t in targets:
        if t.default:
            assert un.delete_target(t) is None, t
    assert not env["appdata"].exists()
    assert not (env["dist"] / un.MAIN_EXE).exists()
    assert not env["moved"].exists()
    assert not env["ours"].exists()
    assert env["other"].exists()
    assert env["victim"].exists()
    assert env["sessions_root"].exists()
    assert env["me"].exists()  # 自分自身は終了後に別プロセスで消す


def test_delete_everything_when_user_selects_all(env):
    for t in un.find_targets(env["me"]):
        assert un.delete_target(t) is None
    assert not env["sessions_root"].exists()


def test_safety_check_refuses_unexpected_paths(env, tmp_path):
    bad = [
        un.Target("appdata", "x", tmp_path / "home", True, True),
        un.Target("sessions", "x", tmp_path / "home" / "Documents", True, True),
        un.Target("exe0", "x", env["victim"], False, True),
        un.Target("mei:_MEI99999", "x", env["other"], True, True),
        un.Target("self", "x", env["victim"], False, True),
    ]
    for t in bad:
        assert un.delete_target(t) == "安全確認に失敗したため削除しませんでした"
    assert (tmp_path / "home").exists() and env["victim"].exists() and env["other"].exists()


def test_missing_targets_are_reported_but_harmless(env):
    import shutil

    shutil.rmtree(env["appdata"])
    t = next(t for t in un.find_targets(env["me"]) if t.key == "appdata")
    assert not t.exists
    assert un.delete_target(t) is None


def test_main_app_records_its_location(env, monkeypatch, tmp_path):
    exe = tmp_path / "Somewhere" / un.MAIN_EXE
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(exe))
    config.record_exe_location()
    config.record_exe_location()  # 重複して記録しない
    info = json.loads(config.install_info_path().read_text())
    assert info["exe_paths"].count(str(exe.resolve())) == 1
    assert any(t.path == exe.resolve() for t in un.find_targets(None))
