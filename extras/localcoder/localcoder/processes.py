"""LocalCoder が起こしたプロセス (LocalCoder 自身・llama-server) の記録と、非常停止。

記録: <置き場所>/logs/processes.json に {名前: {pid, image, created}}。
PID は Windows でも使い回されるので、止める前に「実行ファイルのパス」と「起動した時刻」が記録と同じかを確かめ、
違えば (別のプログラムが同じ番号を使っている) 止めない。

    LocalCoderCLI.exe --force-stop     画面が固まったときなどに、記録した LocalCoder と llama-server を止める
"""

import ctypes
import json
import os
import signal
import subprocess
import time

STATE = "processes.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def popen_group_options():
    """子プロセスを、まとめて止められる形で起こすための Popen の引数。"""
    if os.name == "nt":
        return {"creationflags": NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _windows_identity(pid):
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(buf))
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None

        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]
        created, exited, kern, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kern),
                                   ctypes.byref(user)):
            return None
        code = ctypes.c_ulong()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:      # 259 = STILL_ACTIVE
            return None
        return {"image": os.path.normcase(buf.value), "created": (created.hi << 32) | created.lo}
    finally:
        k32.CloseHandle(h)


def process_identity(pid):
    """動いているプロセスの {image, created}。無ければ None。"""
    try:
        if os.name == "nt":
            return _windows_identity(pid)
        with open(f"/proc/{int(pid)}/stat", encoding="ascii", errors="replace") as f:
            stat = f.read()
        if stat.rsplit(")", 1)[1].split()[0] == "Z":            # 終わったが回収されていない
            return None
        started = int(stat.rsplit(")", 1)[1].split()[19])       # 起動した時刻 (起動からの tick)
        return {"image": os.path.normcase(os.path.realpath(f"/proc/{int(pid)}/exe")), "created": started}
    except (OSError, ValueError, IndexError, TypeError, AttributeError):
        return None


def _state_path(home):
    return os.path.join(home, "logs", STATE)


def _load(home):
    try:
        with open(_state_path(home), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(home, data):
    path = _state_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def record_process(home, key, pid):
    ident = process_identity(pid)
    if not home or not ident:
        return
    try:
        data = _load(home)
        data[key] = dict(ident, pid=int(pid))
        _save(home, data)
    except OSError:
        pass


def clear_process(home, key, pid=None):
    if not home:
        return
    try:
        data = _load(home)
        if key in data and (pid is None or data[key].get("pid") == pid):
            del data[key]
            _save(home, data)
    except OSError:
        pass


def kill_pid_tree(pid):
    """PID と、そこから起きたプロセスをまとめて止める。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))], stdin=subprocess.DEVNULL,
                       capture_output=True, creationflags=NO_WINDOW)
        return
    for kill in (lambda: os.killpg(int(pid), signal.SIGKILL), lambda: os.kill(int(pid), signal.SIGKILL)):
        try:
            kill()
            return
        except OSError:
            continue


def terminate_process_tree(proc, timeout=20):
    """Popen で起こした子を、その子孫ごと止める (まずは穏やかに、だめなら強制)。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    pid = getattr(proc, "pid", None)
    if pid and (proc.poll() is None or os.name == "nt"):
        kill_pid_tree(pid)                   # Windows の terminate は子孫を止めないので、残りもまとめて
    try:
        proc.wait(5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def force_stop(home, say=print):
    """非常停止: 記録した LocalCoder と llama-server を、身元が記録と同じときだけ止める。止めた数を返す。"""
    data = _load(home)
    me = {os.getpid(), os.getppid()}
    stopped, gone = 0, 0
    for key, rec in sorted(data.items()):
        pid = rec.get("pid")
        if not pid or pid in me:
            continue
        now = process_identity(pid)
        if not now or now.get("image") != rec.get("image") or now.get("created") != rec.get("created"):
            gone += 1                        # もう終わっている (番号が別のプログラムに使い回されていても、それは止めない)
            continue
        kill_pid_tree(pid)
        for _ in range(30):                  # 止まるまで少し待つ (最大 3 秒)
            if process_identity(pid) is None:
                break
            time.sleep(0.1)
        if process_identity(pid) is None:
            stopped += 1
            say(f"止めました: {key} (PID {pid})")
        else:
            say(f"止められませんでした: {key} (PID {pid})")
    if stopped:
        say(f"LocalCoder と関係するプロセスを {stopped} 個止めました")
    elif gone:
        say("記録はありましたが、どれももう終わっていました")
    else:
        say("止める対象の LocalCoder は見つかりませんでした")
    if not any(k for k in data if process_identity(data[k].get("pid", 0))):
        try:
            os.remove(_state_path(home))
        except OSError:
            pass
    return stopped
