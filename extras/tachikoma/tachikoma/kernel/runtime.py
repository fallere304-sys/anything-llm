"""実行ループ (カーネル): 思考 (agent) を外側から包み、計測・例外記録・プロファイル・再起動を行う。

agent のコードは進化で変わりうる。だから「何が起きたか」の記録はここで取る:
    step_s            1 回の思考ループの所要時間
    reply_latency_s   話しかけられてから返事を出すまで (on_user_message を外から包んで測る)
    error             例外 (発生場所とトレースバックを errors に。進化の「直す理由」になる)
    watts             消費電力 (1 分ごと。進化の「省電力」「1Wh あたりの知識」の分母)

監督のコマンド (/evolution /freeze /unfreeze /revert /approve /proposals /discoveries) もここで処理する。
思考のコードが書き換わっても、人間が進化を止め・戻す手段は消えない。

CPU の脳 (自己進化用) は、会話中は一時停止して CPU を返し、会話が終わったら再開する。
ときどき 1 step を cProfile で測り、思考のコードの遅い関数を進化エンジンに渡す (高速化の材料)。
"""

import cProfile
import os
import pstats
import sys
import time
import traceback

from . import ROOT, is_evolvable, rel

RESTART_CODE = 75


def instrument(agent, metrics):
    orig = getattr(agent, "on_user_message", None)
    if orig is None:
        return

    def wrapped(*a, **kw):
        t = time.perf_counter()
        try:
            return orig(*a, **kw)
        finally:
            metrics.record("reply_latency_s", time.perf_counter() - t)
    agent.on_user_message = wrapped


OVERSIGHT = ("/evolution", "/freeze", "/unfreeze", "/revert", "/approve", "/proposals", "/discoveries")


def oversight(agent, evolution):
    """人間の監督コマンドを、思考のコードより先に処理する。"""
    orig = agent.on_command

    def wrapped(text):
        cmd, _, arg = text.partition(" ")
        if evolution is None or cmd not in OVERSIGHT:
            return orig(text)
        if cmd == "/freeze":
            evolution.freeze(True)
            agent.say("自己改良を止めました (/unfreeze で再開)。")
        elif cmd == "/unfreeze":
            evolution.freeze(False)
            agent.say("自己改良を再開します。")
        elif cmd == "/revert" and arg.strip().isdigit():
            agent.say(evolution.revert(int(arg), "手動で撤回"))
        elif cmd == "/approve" and arg.strip().isdigit():
            agent.say(evolution.approve(int(arg)))
        elif cmd == "/proposals":
            rows = evolution.db.execute("SELECT id, rationale FROM evolutions WHERE status='pending_approval'").fetchall()
            agent.say("承認待ちのカーネル変更: " + ("; ".join(f"#{r[0]} {r[1][:80]}" for r in rows) or "なし"))
        elif cmd == "/discoveries":
            found = evolution._kv("discoveries") or []
            agent.say("ネットや論文で見当たらなかったやり方で定着した改良: " +
                      ("; ".join(f"#{d['id']} {d['name']}" for d in found[-10:]) or "まだ無い"))
        else:
            rows = evolution.history(8)
            st = evolution.stats()
            lines = []
            for r in rows:
                nv = "-" if r["novelty"] is None else f"{r['novelty']:.2f}"
                lines.append(f"#{r['id']} {r['status']} {r['goal'] or ''}/{r['level']} 新しさ {nv} "
                             f"{(r['rationale'] or '')[:60]}")
            rates = ", ".join(f"{g} {v['a'] / (v['a'] + v['b']):.2f}({v['n']})" for g, v in st.items())
            agent.say(("凍結中 | " if evolution.frozen else "") + " / ".join(lines or ["まだ無い"]) +
                      f" | 目標ごとの成功しやすさ: {rates}")
    agent.on_command = wrapped


def hotspots(profile, root=ROOT, top=5):
    """思考のコードの中で、自分自身の時間 (tottime) が長い関数。"""
    st = pstats.Stats(profile)
    out = []
    for (file, _, func), (_, _, tt, _, _) in st.stats.items():
        r = rel(file, root) if os.path.isabs(file) else file
        if is_evolvable(r) and r.endswith(".py"):
            out.append((r, func, tt))
    out.sort(key=lambda x: -x[2])
    return out[:top]


def run(agent, cfg, metrics, evolution=None, sleep=time.sleep, max_steps=None, brain=None, power=None,
        clock=time.time, foresight=None):
    """ループを回す。再起動が必要になったら RESTART_CODE を返す。"""
    instrument(agent, metrics)
    oversight(agent, evolution)
    n, consecutive_errors = 0, 0
    last_watts = 0.0
    while max_steps is None or n < max_steps:
        n += 1
        t = time.perf_counter()
        prof = cProfile.Profile() if n % cfg["profile_every"] == 0 else None
        try:
            if prof:
                prof.enable()
            agent.step()
            consecutive_errors = 0
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 — 思考の失敗で本体を止めない。記録して進化の材料にする
            consecutive_errors += 1
            metrics.record_error(e, traceback.extract_tb(e.__traceback__), traceback.format_exc())
            agent.log(f"思考ループで例外 (記録済み): {type(e).__name__}: {e}")
            if consecutive_errors >= 5:
                sleep(min(60, 2 ** consecutive_errors))
        finally:
            if prof:
                prof.disable()
                if evolution is not None:
                    evolution.hotspots = hotspots(prof)
        metrics.record("step_s", time.perf_counter() - t)
        now = clock()
        if power is not None and now - last_watts >= 60:
            last_watts = now
            try:
                metrics.record("watts", power.watts(gpu_active_guess=False))
            except Exception:  # noqa: BLE001
                pass
        if foresight is not None:
            try:
                foresight.sync()         # 拾った情報が後で役立ったかを記帳 (思考のコードの外で)
            except Exception as e:  # noqa: BLE001
                metrics.record_error(e, traceback.extract_tb(e.__traceback__), traceback.format_exc())
        mode = getattr(agent, "mode", None)
        if brain is not None:
            try:
                brain.pause() if mode == "conversing" else brain.resume()
            except Exception:  # noqa: BLE001 — CPU の脳の不調で本体を止めない
                pass
        if evolution is not None and mode != "conversing":
            try:
                if evolution.ready():
                    msg = evolution.start()
                    if msg:
                        agent.log(msg)
            except Exception as e:  # noqa: BLE001
                metrics.record_error(e, traceback.extract_tb(e.__traceback__), traceback.format_exc())
        if evolution is not None:
            try:
                for m in evolution.poll():
                    agent.say(m)
            except Exception as e:  # noqa: BLE001
                metrics.record_error(e, traceback.extract_tb(e.__traceback__), traceback.format_exc())
            if evolution.restart_requested:
                return RESTART_CODE
        sleep(cfg["tick_seconds"])
    return 0


def restart(code):
    """supervisor の下なら終了コードで伝え、そうでなければ自分で再実行する。"""
    if os.environ.get("TACHIKOMA_SUPERVISED"):
        sys.exit(code)
    os.execv(sys.executable, [sys.executable, "-m", "tachikoma", *sys.argv[1:]])
