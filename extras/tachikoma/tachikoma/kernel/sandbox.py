"""サンドボックス: タチコマが書き換えたコードを、本番に入れる前に隔離環境で試す。

Docker (既定):  ネットワーク無し・CPU/RAM は予算内・コードは読み取り専用でマウント・/tmp だけ書ける
ローカル:       Docker が無いとき。隔離が弱いので allow_local_sandbox=true のときだけ使う
"""

import os
import subprocess
import sys


class Sandbox:
    def __init__(self, cfg, budget, run=subprocess.run):
        self.cfg, self.budget, self._run = cfg, budget, run
        self.mode = cfg["sandbox"]

    def docker_available(self):
        try:
            r = self._run([self.cfg["docker_bin"], "version", "--format", "{{.Server.Version}}"],
                          capture_output=True, text=True, timeout=20)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def usable(self):
        if self.mode == "docker":
            return self.docker_available()
        return self.mode == "local" and self.cfg["allow_local_sandbox"]

    def test_command(self, workdir):
        inner = ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]
        if self.mode == "docker":
            return [self.cfg["docker_bin"], "run", "--rm", "--network", "none", "--read-only",
                    "--tmpfs", "/tmp:rw,size=512m", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
                    *self.budget.docker_flags(ram_gb=min(2, self.budget.ram_gb)),
                    "-v", f"{os.path.abspath(workdir)}:/work:ro", "-w", "/work",
                    self.cfg["sandbox_image"], *inner]
        return [sys.executable] + inner[1:]

    def run_tests(self, workdir, timeout=900):
        """(合格したか, 出力の末尾) を返す。"""
        if not self.usable():
            return False, "サンドボックスが使えない (Docker が起動していない / ローカル実行が不許可)"
        env = None
        if self.mode == "local":
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                   "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "TEMP": os.environ.get("TEMP", "/tmp"),
                   "TMP": os.environ.get("TMP", "/tmp")}
        try:
            r = self._run(self.test_command(workdir), cwd=workdir if self.mode == "local" else None,
                          capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return False, "テストが時間切れ"
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out[-3000:]
