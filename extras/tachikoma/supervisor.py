"""見守り役 (カーネル): タチコマを子プロセスとして起動し、自己改良後の再起動と、壊れたときの撤回を行う。

    python supervisor.py --config config.json --verbose

- 終了コード 75: 自己改良を反映するための再起動 → すぐ起動し直す
- 異常終了が 10 分以内に 3 回: 最新の自己改良 (コード) を自動で撤回してから起動し直す
- Ctrl+C / 正常終了: 終わる
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tachikoma import config  # noqa: E402
from tachikoma.kernel.evolve import emergency_revert  # noqa: E402
from tachikoma.kernel.runtime import RESTART_CODE  # noqa: E402


def main(argv=None, popen=subprocess.call, clock=time.time, max_runs=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg_path = argv[argv.index("--config") + 1] if "--config" in argv else "config.json"
    env = dict(os.environ, TACHIKOMA_SUPERVISED="1")
    crashes, runs = [], 0
    while max_runs is None or runs < max_runs:
        runs += 1
        try:
            code = popen([sys.executable, "-m", "tachikoma", *argv], cwd=HERE, env=env)
        except KeyboardInterrupt:
            return 0
        if code == RESTART_CODE:
            print("[supervisor] 自己改良を反映して再起動します")
            continue
        if code in (0, 130, -2, 3221225786):     # 正常終了 / Ctrl+C (Windows の STATUS_CONTROL_C_EXIT 含む)
            return 0
        now = clock()
        crashes = [t for t in crashes if now - t < 600] + [now]
        print(f"[supervisor] 異常終了 (code={code})")
        if len(crashes) >= 3:
            cfg = config.load(cfg_path)
            msg = emergency_revert(cfg["db_path"], cfg, HERE)
            print(f"[supervisor] {msg or '撤回できる自己改良がありません'}")
            crashes = []
            if msg is None:
                time.sleep(30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
