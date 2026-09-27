"""消費電力の計測。独りの時間に何をするかを「1 Wh あたりの伸び」で選ぶための分母。

- GPU: nvidia-smi の power.draw (実測。GTX 1060 の TDP は 120W)
- CPU: 実測手段が無いので、使用率 × TDP (i7-7700 は 65W) で推定する [推定値であることを明示]
"""

import subprocess
import time


class PowerMeter:
    def __init__(self, cfg, run=subprocess.run, clock=time.monotonic, cpu_percent=None):
        self.cfg, self._run, self.clock = cfg, run, clock
        self._cpu_percent = cpu_percent
        self._gpu_ok = True
        self._cache = (0.0, None)

    def gpu_watts(self):
        if not self._gpu_ok:
            return None
        t, v = self._cache
        if self.clock() - t < 2.0:
            return v
        try:
            r = self._run(["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
                          capture_output=True, text=True, timeout=5)
            v = sum(float(x) for x in r.stdout.split() if x.replace(".", "", 1).isdigit()) if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            v = None
        if v is None:
            self._gpu_ok = False
        self._cache = (self.clock(), v)
        return v

    def cpu_watts(self):
        pct = None
        if self._cpu_percent is not None:
            pct = self._cpu_percent()
        else:
            try:
                import psutil
                pct = psutil.cpu_percent(interval=None)
            except ImportError:
                pct = None
        if pct is None:
            return None
        return self.cfg["cpu_tdp_w"] * pct / 100.0

    def watts(self, gpu_active_guess=False):
        """いまの消費電力の推定 (W)。測れないときは設定値の目安で代用する。"""
        g = self.gpu_watts()
        if g is None:
            g = self.cfg["gpu_tdp_w"] * (0.8 if gpu_active_guess else 0.1)
        c = self.cpu_watts()
        if c is None:
            c = self.cfg["cpu_tdp_w"] * 0.3
        return g + c
