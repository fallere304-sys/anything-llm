"""UI: ブラウザで開くタチコマの画面 (http://127.0.0.1:8765)。

- タチコマの画像が、発話の内容 (喜ぶ・首をかしげる・困る…) と状態 (考え中・聞き取り練習中・
  読み取り練習中・学習中・お休み) に合わせて動く
- 画像は利用者が ui/assets/ に置く (tachikoma.png、表情別に tachikoma_happy.png など)。
  置かれていなければ同梱の仮アバター (オリジナルの簡単なロボット) を使う
- 会話はこの画面からも打てる。音声会話と同じ流れで扱う
- カメラとマイクのスイッチ (kernel/switches.py)。画面は「切り替えて」と頼むだけで、切り替えは本体のループで行う
- 「画像を変える」で選んだ画像は、画面 (ブラウザ) の中で背景を抜いてから ui/assets/tachikoma.png に保存する。
  この PC の中だけで扱い、外には出さない

自分の PC からしか開けないよう 127.0.0.1 で待ち受け、他のサイトからの送信 (CSRF) は
独自ヘッダと Origin の確認で拒否する。標準ライブラリのみ。
"""

import json
import os
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "static")
_ASSET = re.compile(r"^[a-z0-9_]+\.(png|gif|webp|jpg|jpeg|svg)$")
PNG = b"\x89PNG\r\n\x1a\n"
MAX_AVATAR = 6 * 1024 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".gif": "image/gif", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class UIServer:
    def __init__(self, cfg, host="127.0.0.1", port=None, static_dir=STATIC):
        self.cfg, self.host = cfg, host
        self.port = cfg["ui_port"] if port is None else port
        self.static = static_dir
        self.assets = os.path.abspath(cfg["ui_assets_dir"])
        self.inbox = queue.Queue()
        self.commands = queue.Queue()      # 画面からの頼みごと (スイッチ)。本体のループが処理する
        self.switches = None
        self.clients = []
        self.lock = threading.Lock()
        self.state = {"type": "state"}
        self.server = None

    # ------------------------------------------------------------ 送信
    def push(self, event):
        if event.get("type") == "state":
            if all(self.state.get(k) == v for k, v in event.items()):
                return
            self.state.update(event)
            event = dict(self.state)       # 一部だけの更新でも、画面には全体を送る
        with self.lock:
            for q in list(self.clients):
                q.put(event)

    def asset_names(self):
        if not os.path.isdir(self.assets):
            return []
        return sorted(f for f in os.listdir(self.assets) if _ASSET.match(f))

    # ------------------------------------------------------------ サーバー
    def start(self):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _file(self, directory, name):
                path = os.path.join(directory, name)
                if not os.path.isfile(path):
                    return self._send(404, b"not found")
                with open(path, "rb") as f:
                    self._send(200, f.read(), TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))

            def do_GET(self):
                p = self.path.split("?")[0]
                if p == "/":
                    return self._file(ui.static, "index.html")
                if p.startswith("/static/") and re.match(r"^[a-z0-9_.-]+$", p[8:]) and ".." not in p:
                    return self._file(ui.static, p[8:])
                if p.startswith("/assets/") and _ASSET.match(p[8:]):
                    return self._file(ui.assets, p[8:])
                if p == "/assets.json":
                    return self._send(200, json.dumps(ui.asset_names()).encode(), "application/json")
                if p == "/state":
                    return self._send(200, json.dumps(ui.state, ensure_ascii=False).encode(), "application/json")
                if p == "/events":
                    return self._events()
                return self._send(404, b"not found")

            def _events(self):
                q = queue.Queue()
                with ui.lock:
                    ui.clients.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    self.wfile.write(f"data: {json.dumps(ui.state, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            ev = q.get(timeout=15)
                            self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with ui.lock:
                        ui.clients.remove(q)

            def do_POST(self):
                p = self.path.split("?")[0]
                if p not in ("/say", "/switch", "/avatar", "/avatar/reset"):
                    return self._send(404, b"not found")
                origin = self.headers.get("Origin")
                allowed = {f"http://127.0.0.1:{ui.port}", f"http://localhost:{ui.port}"}
                if self.headers.get("X-Tachikoma") != "1" or (origin and origin not in allowed):
                    return self._send(403, b"forbidden")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self._send(400, b"bad request")
                if p == "/avatar":
                    return self._avatar(length)
                if p == "/avatar/reset":
                    return self._avatar_reset()
                try:
                    body = json.loads(self.rfile.read(min(length, 8192)).decode("utf-8"))
                except (ValueError, AttributeError):
                    return self._send(400, b"bad request")
                if not isinstance(body, dict):
                    return self._send(400, b"bad request")
                if p == "/switch":
                    name, on = body.get("name"), body.get("on")
                    if name not in ("camera", "mic") or not isinstance(on, bool):
                        return self._send(400, b"bad request")
                    ui.commands.put(("switch", name, on))
                    return self._send(202)
                text = str(body.get("text", "")).strip()[:2000]
                if text:
                    ui.inbox.put(text)
                self._send(204)

            def _avatar(self, length):
                if not 0 < length <= MAX_AVATAR:
                    return self._send(413, b"too large")
                data = self.rfile.read(length)
                if not data.startswith(PNG):
                    return self._send(415, b"png only")
                os.makedirs(ui.assets, exist_ok=True)
                tmp = os.path.join(ui.assets, "tachikoma.png.part")
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, os.path.join(ui.assets, "tachikoma.png"))
                ui.push({"type": "assets"})
                self._send(204)

            def _avatar_reset(self):
                for name in ui.asset_names():
                    if name.startswith("tachikoma.") or name.startswith("tachikoma_"):
                        os.remove(os.path.join(ui.assets, name))
                ui.push({"type": "assets"})
                self._send(204)

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://{self.host}:{self.port}/"

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class UISensor:
    """画面から打たれた言葉を、話しかけとして渡す。スイッチの頼みごともここ (本体のループ) で処理する。"""
    name = "user"

    def __init__(self, ui):
        self.ui = ui

    def poll(self):
        while not self.ui.commands.empty():
            kind, name, on = self.ui.commands.get_nowait()
            if kind == "switch" and self.ui.switches is not None:
                self.ui.switches.set(name, on)
        out = []
        while not self.ui.inbox.empty():
            out.append(("user_message", self.ui.inbox.get_nowait()))
        return out
