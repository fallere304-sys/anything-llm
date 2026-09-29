"""LocalCoder: 日本語で頼むと、この PC の中だけでプログラムを書いて・動かして・直して・.exe にする相棒。

    LocalCoder.exe                    初回は置き場所 (SSD) とモデルを選んで準備し、対話を始める
    LocalCoder.exe --setup            置き場所・モデルを選び直す
    LocalCoder.exe --task "…" --yes   1 つの頼みをやり切って終わる (確認なし)
    LocalCoder.exe --uninstall        取り除いて入れる前の姿に戻す (LocalCoderUninstall.exe と同じ)

    LocalCoder.exe --gpu-memory normal  共有 GPU メモリを使わない (既定は shared: PC のメモリも GPU 用に使う)

対話中のコマンド: /new (話を切り替える)  /cd フォルダ (作業フォルダ)  /auto (コマンドを毎回確かめない)
                  /gpu shared|normal (共有 GPU メモリを使うか。次の起動から)  /status  /help  /exit
外へ出る通信は、準備のときの取得 (llama.cpp・モデル・Python) だけ。頼みごとやコードは外に出ない。
"""

import argparse
import json
import os
import re
import string
import subprocess
import sys

from . import models, server, toolchain, uninstall
from .agent import Agent, Client, ModelError
from .tools import Tools

CONFIG = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "LocalCoder", "config.json")


def say(*a, **k):
    try:
        print(*a, **k, flush=True)
    except UnicodeEncodeError:
        print(*(str(x).encode("ascii", "backslashreplace").decode() for x in a), **k, flush=True)


def load(path=CONFIG):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(cfg, path=CONFIG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cfg.items() if not k.startswith("_")}, f, ensure_ascii=False, indent=2)


def ask(q, default=""):
    try:
        a = input(f"{q} " + (f"[{default}] " if default else "")).strip()
    except EOFError:
        return default
    return a or default


# ---------------------------------------------------------------- 準備
def drives():
    """[(ドライブ, 空き GB, 種類)]。種類は PowerShell で分かれば SSD / HDD。"""
    kinds = {}
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Partition | Where-Object DriveLetter | ForEach-Object { $d = Get-PhysicalDisk | "
                 "Where-Object DeviceId -eq $_.DiskNumber; \"$($_.DriveLetter)`t$($d.MediaType)\" }"],
                capture_output=True, text=True, timeout=30).stdout
            for line in out.splitlines():
                letter, _, media = line.partition("\t")
                kinds[letter.strip().upper()] = media.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        roots = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    else:
        roots = [os.path.expanduser("~")]
    return [(r, server.free_gb(r), kinds.get(r[0].upper(), "")) for r in roots]


def setup(cfg, interactive, model_key=None, path=CONFIG):
    ds = sorted(drives(), key=lambda d: -d[1])
    if interactive:
        say("\nモデルを置く場所を選んでください (SSD がおすすめ。大きいモデルは 60〜70GB 使います)")
        for i, (r, gb, kind) in enumerate(ds, 1):
            say(f"  {i}. {r}  空き {gb:.0f} GB  {kind}")
        i = ask("番号:", "1")
        root = ds[int(i) - 1][0] if i.isdigit() and 0 < int(i) <= len(ds) else ds[0][0]
        home = ask("フォルダ:", os.path.join(root, "LocalCoder"))
    else:
        home = cfg.get("home") or os.path.join(ds[0][0], "LocalCoder")
    os.makedirs(home, exist_ok=True)
    free = server.free_gb(home)
    entry = models.by_key(model_key) if model_key else None
    if entry is None and interactive:
        say(f"\n考える力 (モデル) を選んでください。空き {free:.0f} GB。大きいほど賢く、遅い")
        for i, m in enumerate(models.CATALOG, 1):
            warn = "  ← 空きが足りません" if m["gb"] + 5 > free else ""
            say(f"  {i}. {m['label']}  約 {m['gb']} GB  — {m['note']}{warn}")
        i = ask("番号:", "1")
        entry = models.CATALOG[int(i) - 1] if i.isdigit() and 0 < int(i) <= len(models.CATALOG) else models.CATALOG[0]
    entry = entry or models.CATALOG[0]
    tools_ok, gpu = True, cfg.get("gpu_memory", "shared")
    if interactive:
        tools_ok = ask("プログラムを .exe にする道具 (Python・約 150MB) も用意しますか？ [Y/n]", "Y").lower() != "n"
        gpu = "normal" if ask("GPU のメモリに入りきらない分を、PC のメモリ (共有 GPU メモリ) に置きますか？"
                              " (大きいモデルを GPU で動かせる。速くなるとは限らない) [Y/n]", "Y").lower() == "n" else "shared"
    cfg.update(home=home, model_key=entry["key"], model=None, toolchain=tools_ok, gpu_memory=gpu)
    save(cfg, path)
    return cfg


def prepare(cfg, args, kinds):
    home = cfg["home"]
    ready = [k for k in kinds if server.find_server(os.path.join(home, "llama", k))]
    if len(ready) < len(kinds):
        ready = server.install(home, kinds, say)
    model = args.model_file or cfg.get("model")
    if not model or not os.path.exists(model):
        entry = models.by_key(cfg.get("model_key") or "standard")
        model = models.fetch(entry, os.path.join(home, "models"), say)
        cfg["model"] = model
        save(cfg, args.config)
    if cfg.get("toolchain", True):
        toolchain.ensure(home, say)
    return model


# ---------------------------------------------------------------- 対話
def confirm_factory(state, interactive):
    def confirm(command):
        if state["auto"] or not interactive:
            return state["auto"]
        a = ask(f"    このコマンドを実行してよい？ [Y/n/a=以後は聞かない]\n    {command}\n   ", "Y").lower()
        if a == "a":
            state["auto"] = True
        return a in ("y", "yes", "a", "はい")
    return confirm


def repl(agent, tools, state, cfg):
    say("\n日本語で頼んでください (例: 「CSV を読んで集計する画面つきのツールを作って .exe にして」)。/help で使い方")
    while True:
        try:
            text = input("\nあなた> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not text:
            continue
        if text in ("/exit", "/quit", "終わり"):
            return
        if text == "/help":
            say(__doc__)
            continue
        if text == "/new":
            agent.reset()
            say("話を切り替えました")
            continue
        if text == "/auto":
            state["auto"] = not state["auto"]
            say("コマンドを毎回確かめません" if state["auto"] else "コマンドを実行する前に確かめます")
            continue
        if text == "/status":
            say(f"置き場所 {cfg['home']} / モデル {os.path.basename(cfg.get('model') or '')} / 作業フォルダ {tools.root}"
                f" / GPU {cfg.get('kind', '?')} 版・共有 GPU メモリ {'使用中' if cfg.get('shared_now') else '不使用'}")
            continue
        m = re.match(r"^/gpu\s+(shared|normal)$", text)
        if m:
            cfg["gpu_memory"] = m.group(1)
            save(cfg, cfg.get("_path", CONFIG))
            say("次に起動したときから、" + ("共有 GPU メモリも使います" if m.group(1) == "shared" else
                                             "共有 GPU メモリを使いません (MoE の専門家は CPU 側に置きます)"))
            continue
        m = re.match(r"^/cd\s+(.+)$", text)
        if m:
            p = m.group(1).strip().strip('"')
            p = p if os.path.isabs(p) else os.path.join(cfg["home"], "workspace", p)
            agent.tools = tools = Tools(p, env=tools.env)
            agent.reset()
            say(f"作業フォルダ: {tools.root}")
            continue
        say("")
        try:
            agent.ask(text)
        except ModelError as e:
            say(f"\n(モデルとのやりとりに失敗しました: {e})")
        except KeyboardInterrupt:
            say("\n(止めました)")


def check_gpu(kinds, gpu_memory, info=None):
    """NVIDIA の GPU とドライバを見て知らせ、実際に使う置き方を返す。共有 GPU メモリはドライバ 536.40 以降の機能。"""
    info = info if info is not None else server.gpu_info()
    if not info or not any(k in kinds for k in ("cuda", "vulkan")):
        return gpu_memory
    say(f"GPU: {info['name']} ({info['vram_mb'] / 1024:.0f}GB)・ドライバ {info['driver']}")
    if gpu_memory == "shared":
        if server.driver_has_sysmem_fallback(info["driver"]):
            say("  共有 GPU メモリも使います (入りきらない分は PC のメモリへ。NVIDIA コントロールパネルの"
                "「CUDA - システム メモリ フォールバック ポリシー」が「優先しない」になっていると使えません)")
        else:
            say("  このドライバは古く、共有 GPU メモリに置けません (536.40 以降が必要)。いつもの置き方で動かします")
            return "normal"
    return gpu_memory


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--uninstall" in argv:
        argv.remove("--uninstall")
        return uninstall.main(argv)
    ap = argparse.ArgumentParser(prog="LocalCoder", description="日本語で頼むと、プログラムを作る相棒 (ローカル)")
    ap.add_argument("--setup", action="store_true", help="置き場所・モデルを選び直す")
    ap.add_argument("--home", help="置き場所 (SSD のフォルダ)")
    ap.add_argument("--model", dest="model_key", choices=[m["key"] for m in models.CATALOG])
    ap.add_argument("--model-file", help="手元の GGUF を使う")
    ap.add_argument("--kinds", default=",".join(server.KINDS), help="試す llama.cpp の組 (cuda,vulkan,cpu)")
    ap.add_argument("--workspace", help="作業フォルダ")
    ap.add_argument("--task", help="1 つの頼みをやり切って終わる")
    ap.add_argument("--yes", action="store_true", help="コマンドを確かめずに実行する")
    ap.add_argument("--no-toolchain", action="store_true", help=".exe にする道具を用意しない")
    ap.add_argument("--gpu-memory", choices=["shared", "normal"],
                    help="shared: GPU に入りきらない分を共有 GPU メモリ (PC のメモリ) に置く / normal: 置かない")
    ap.add_argument("--ctx", type=int, default=32768)
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--config", default=CONFIG)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # 画面 (コンソール) ならそのまま。ファイルや別のプログラムに渡すときは UTF-8 で書く (日本語が ??? にならない)
        if sys.stdout.isatty():
            sys.stdout.reconfigure(errors="replace")
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    interactive = sys.stdin is not None and sys.stdin.isatty() and not args.task
    cfg = load(args.config)
    if not cfg:
        cfg["before"] = uninstall.snapshot()      # 入れる前の姿 (取り除くとき、もともとあったものを残すため)
    if getattr(sys, "frozen", False):
        cfg["launched_from"] = list(dict.fromkeys(cfg.get("launched_from", []) + [os.path.abspath(sys.executable)]))
    if args.home:
        cfg["home"] = args.home
    if args.no_toolchain:
        cfg["toolchain"] = False
    if args.setup or not cfg.get("home") or (args.model_key and args.model_key != cfg.get("model_key")):
        cfg = setup(cfg, interactive, args.model_key, args.config)
    if args.no_toolchain:
        cfg["toolchain"] = False
    cfg["homes"] = list(dict.fromkeys(cfg.get("homes", []) + [cfg["home"]]))   # 選び直しても、前の置き場所を忘れない
    save(cfg, args.config)
    kinds = [k for k in args.kinds.split(",") if k in server.KINDS]
    try:
        model = prepare(cfg, args, kinds)
    except OSError as e:
        say(f"準備できませんでした: {e}")
        return 2
    if args.gpu_memory:
        cfg["gpu_memory"] = args.gpu_memory
    gpu_memory = cfg.setdefault("gpu_memory", "shared")
    gpu_memory = check_gpu(kinds, gpu_memory)
    srv = server.Server(cfg["home"], port=args.port, ctx=args.ctx, say=say, gpu_memory=gpu_memory)
    order = [cfg["kind"]] + [k for k in kinds if k != cfg.get("kind")] if cfg.get("kind") in kinds else kinds
    kind = srv.start_best(model, order)
    if not kind:
        say(f"モデルを動かせませんでした。記録: {srv.log_path}")
        return 3
    cfg["kind"], cfg["shared_now"], cfg["_path"] = kind, srv.shared, args.config
    save(cfg, args.config)
    state = {"auto": bool(args.yes)}
    ws = args.workspace or os.path.join(cfg["home"], "workspace", "default")
    tools = Tools(ws, env=toolchain.env(cfg["home"]))
    # 文脈の上限 (字): 日本語は 1 トークン ≒ 1〜1.5 字。指示文と道具の説明 (約 2000 トークン) の分を残す
    agent = Agent(Client(srv.url), tools, confirm=confirm_factory(state, interactive), out=say,
                  ctx_chars=max(4000, int((args.ctx - 2500) * 1.2)))
    say(f"作業フォルダ: {tools.root}")
    try:
        if args.task:
            say(f"\nあなた> {args.task}\n")
            agent.ask(args.task)
            return 0 if agent.finished else 5          # 報告まで行かなかった (繰り返し・回数切れ)
        repl(agent, tools, state, cfg)
        return 0
    except ModelError as e:
        say(f"モデルとのやりとりに失敗しました: {e}")
        return 4
    finally:
        srv.stop()


if __name__ == "__main__":
    sys.exit(main())
