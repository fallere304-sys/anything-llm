"""画面の自己点検 (LocalCoder.exe --selftest 結果.txt)。CI で、exe の中の画面が本当に動くかを確かめる。

本物の画面・エージェント・モデルとのやりとりを、台本どおりに答える偽のモデル (この中で動く) につないで:
1. 頼みを送る → モデルの考え・本文・道具 (list_files) と結果・報告が画面に出る
2. 返事が遅い頼みを送り、1 秒後に「止める」→ 数秒以内に止まり、次の頼みを受けられる
3. 「モデルの考えを表示する」を切ると、考えが隠れる
結果を 1 行ずつファイルに書く (最後の行が OK か NG)。
"""

import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agent import Agent, Client
from .gui import App
from .tools import Tools

SCRIPT = [
    {"think": ["まず作業フォルダに", "何があるか見る"], "text": "フォルダの中を見ます。", "call": ("list_files", {})},
    {"think": ["空なので、そう伝える"], "text": "作業フォルダは空でした。終わりました。"},
    {"delay": 30, "text": "(届かないはずの返事)"},
]


class _Fake(BaseHTTPRequestHandler):
    script = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        step = _Fake.script.pop(0) if _Fake.script else {"text": "はい"}
        chunks = [{"choices": [{"delta": {"reasoning_content": t}}]} for t in step.get("think", [])]
        if step.get("text"):
            chunks.append({"choices": [{"delta": {"content": step["text"]}}]})
        if step.get("call"):
            name, args = step["call"]
            chunks.append({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c0", "function": {
                "name": name, "arguments": json.dumps(args)}}]}}]})
        chunks.append({"choices": [], "timings": {"predicted_per_second": 12.0}})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            end = time.time() + step.get("delay", 0)
            while time.time() < end:          # 頼みを読み込んでいる間 (何も届かない)
                time.sleep(0.1)
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"data: [DONE]\n\n")
        except OSError:
            pass


def run(root, args, out_path, timeout=90):
    _Fake.script = [dict(s) for s in SCRIPT]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    work = tempfile.mkdtemp(prefix="localcoder-selftest-")

    def boot(app):
        agent = Agent(Client(url), Tools(work), confirm=app.confirm, on_event=app.on_event)
        return {"model": "selftest.gguf", "kind": "selftest"}, None, agent, agent.tools

    app = App(root, args, boot=boot)
    report, result = [], {"ok": False}
    t_end = time.time() + timeout

    def check(name, cond):
        report.append(f"{'ok' if cond else 'NG'}  {name}")
        return cond

    def finish(ok):
        result["ok"] = ok
        report.append("OK" if ok else "NG")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
        app.close()

    def wait(cond, then):
        if time.time() > t_end:
            report.append("NG  時間切れ")
            finish(False)
        elif cond():
            then()
        else:
            root.after(100, lambda: wait(cond, then))

    def step1():
        app.input.insert("1.0", "作業フォルダに何があるか見て\n(2 行目も送れるか)")
        app.send()
        wait(lambda: not app.busy, step2)

    def step2():
        log = app.log.get("1.0", "end")
        good = all([check("頼みが 2 行のまま届いた", "(2 行目も送れるか)" in log),
                    check("モデルの考えが出た", "何があるか見る" in log and bool(app.log.tag_ranges("think"))),
                    check("道具を使ったことが出た", "→ list_files" in log),
                    check("報告が出た", "終わりました" in log),
                    check("速さが出た", "トークン/秒" in log),
                    check("考えは本文に混ぜない", "何があるか見る" not in app.agent.messages[-1]["content"])])
        if not good:
            return finish(False)
        app.send("ゆっくり考えて")
        t0 = time.time()
        root.after(1000, app.stop)
        wait(lambda: not app.busy and time.time() - t0 > 1, lambda: step3(t0))

    def step3(t0):
        took = time.time() - t0
        log = app.log.get("1.0", "end")
        good = all([check(f"止めるで止まった ({took:.1f} 秒)", took < 8),
                    check("止めたと出た", "止めました" in log),
                    check("止めたあとも頼める (ボタンが戻った)", str(app.send_btn["state"]) == "normal")])
        app.think_var.set(False)
        app._apply_think()
        good = good and check("考えを隠せる", str(app.log.tag_cget("think", "elide")) in ("1", "true", "True"))
        finish(good)

    wait(lambda: app.agent is not None and not app.busy, step1)
    root.mainloop()
    httpd.shutdown()
    return 0 if result["ok"] else 1
