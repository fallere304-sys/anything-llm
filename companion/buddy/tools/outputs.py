"""成果物の保存(作業フォルダ outputs/ に新規作成。既存ファイルは上書きしない)。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Union

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def slugify(title: str) -> str:
    s = _BAD.sub("_", title).strip(" ._") or "output"
    return s[:40]


def save_output(outputs: Path, title: str, ext: str, data: Union[str, bytes]) -> Path:
    outputs.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}_{slugify(title)}"
    path, n = outputs / f"{stem}{ext}", 1
    while path.exists():  # 上書きしない
        n += 1
        path = outputs / f"{stem}-{n}{ext}"
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path
