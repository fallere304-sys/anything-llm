"""モデルの自動ダウンロード（＝初回起動時の環境構築）。

- Whisper: Hugging Face の CTranslate2 変換済みモデル (Systran/faster-whisper-*)
- LLM    : Hugging Face の GGUF ファイル（既定 Qwen2.5-3B-Instruct Q4_K_M）

どちらも %LOCALAPPDATA%\\DualMicTranscriber\\models に保存し、2回目以降は
ネットワーク不要。起動直後にバックグラウンドで先行ダウンロードし、
処理パイプラインからも同じ関数を呼ぶ（ロックで二重ダウンロードを防ぐ）。
"""
from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path
from typing import Callable

from . import config
from .errors import Cancelled

log = logging.getLogger(__name__)

Report = Callable[[float | None, str], None]  # (進捗0..1 or None, メッセージ)
_COMPLETE_MARK = ".download_complete"


def _noop(_p, _m):
    pass


class ModelManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._lock = threading.Lock()
        self.status = {"whisper": "未確認", "llm": "未確認"}

    # ---------- 共通 ----------
    def _acquire(self, key: str, cancel: threading.Event | None, report: Report):
        while not self._lock.acquire(timeout=0.5):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            report(None, f"バックグラウンドで準備中: {self.status[key]}")

    # ---------- Whisper ----------
    def whisper_dir(self) -> Path:
        name = str(self.cfg["whisper_model"]).replace("/", "--")
        return config.models_dir() / f"whisper-{name}"

    def whisper_ready(self) -> bool:
        d = self.whisper_dir()
        return (d / _COMPLETE_MARK).exists() and (d / "model.bin").exists()

    def ensure_whisper(self, cancel: threading.Event | None = None, report: Report = _noop) -> Path:
        if self.whisper_ready():
            self.status["whisper"] = "準備完了"
            return self.whisper_dir()
        self._acquire("whisper", cancel, report)
        try:
            if self.whisper_ready():
                return self.whisper_dir()
            from faster_whisper.utils import download_model

            d = self.whisper_dir()
            d.mkdir(parents=True, exist_ok=True)
            msg = f"Whisper「{self.cfg['whisper_model']}」をダウンロード中（初回のみ・数百MB〜数GB）"
            self.status["whisper"] = "ダウンロード中"
            report(None, msg)
            log.info(msg)
            download_model(str(self.cfg["whisper_model"]), output_dir=str(d))
            (d / _COMPLETE_MARK).write_text("ok", encoding="utf-8")
            self.status["whisper"] = "準備完了"
            return d
        except Cancelled:
            self.status["whisper"] = "中断"
            raise
        except Exception as e:
            self.status["whisper"] = f"失敗: {e}"
            raise RuntimeError(f"Whisper モデルのダウンロードに失敗しました: {e}") from e
        finally:
            self._lock.release()

    # ---------- LLM ----------
    def llm_path(self) -> Path:
        return config.models_dir() / "llm" / str(self.cfg["llm_file"])

    def llm_ready(self) -> bool:
        p = self.llm_path()
        return p.exists() and p.stat().st_size > 0

    def ensure_llm(self, cancel: threading.Event | None = None, report: Report = _noop) -> Path:
        if self.llm_ready():
            self.status["llm"] = "準備完了"
            return self.llm_path()
        self._acquire("llm", cancel, report)
        try:
            if self.llm_ready():
                return self.llm_path()
            url = f"https://huggingface.co/{self.cfg['llm_repo']}/resolve/main/{self.cfg['llm_file']}"
            self.status["llm"] = "ダウンロード中"

            def _rep(p, m):
                if p is not None:
                    self.status["llm"] = f"ダウンロード中 {p * 100:.0f}%"
                report(p, m)

            _download(url, self.llm_path(), cancel, _rep, label="ローカルAIモデル")
            self.status["llm"] = "準備完了"
            return self.llm_path()
        except Cancelled:
            self.status["llm"] = "中断"
            raise
        except Exception as e:
            self.status["llm"] = f"失敗: {e}"
            raise RuntimeError(f"ローカルAIモデルのダウンロードに失敗しました: {e}") from e
        finally:
            self._lock.release()

    def prefetch_all(self, stop: threading.Event):
        """起動時のバックグラウンド先行ダウンロード。失敗しても処理時に再試行される。"""
        for fn in (self.ensure_whisper, self.ensure_llm):
            if stop.is_set():
                return
            try:
                fn(cancel=stop)
            except Cancelled:
                return
            except Exception:
                log.exception("先行ダウンロード失敗（処理時に再試行します）")


def _download(url: str, dest: Path, cancel: threading.Event | None, report: Report, label: str):
    """途中再開に対応した単純な HTTP ダウンロード。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "DualMicTranscriber/1.0"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    log.info("download %s -> %s (resume from %d)", url, dest, have)
    with urllib.request.urlopen(req, timeout=60) as resp:
        if have and resp.status != 206:
            have = 0  # サーバーが Range 非対応なら最初から
        total = resp.headers.get("Content-Length")
        total = int(total) + have if total else None
        mode = "ab" if have else "wb"
        done = have
        last_pct = -1
        with open(part, mode) as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = int(done * 100 / total)
                    if pct != last_pct:
                        last_pct = pct
                        report(done / total, f"{label}をダウンロード中 {done / 1e9:.2f} / {total / 1e9:.2f} GB（初回のみ）")
                else:
                    report(None, f"{label}をダウンロード中 {done / 1e9:.2f} GB（初回のみ）")
    if total and done < total:
        raise RuntimeError("ダウンロードが途中で切断されました。再実行すると続きから再開します。")
    part.replace(dest)
