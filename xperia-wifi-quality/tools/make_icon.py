#!/usr/bin/env python3
"""ランチャーアイコン (Wi-Fi 扇形 + 棒グラフ) を依存ライブラリなしで PNG 生成する。"""
import math, os, struct, sys, zlib

SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}

def render(n):
    ss = 4  # スーパーサンプリング
    px = []
    for y in range(n):
        row = []
        for x in range(n):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    u = (x + (sx + 0.5) / ss) / n
                    v = (y + (sy + 0.5) / ss) / n
                    c = sample(u, v)
                    a = c[3] / 255.0
                    for i in range(3):
                        acc[i] += c[i] * a
                    acc[3] += a
            k = ss * ss
            a = acc[3] / k
            if a > 0:
                row.append(tuple(int(acc[i] / acc[3]) for i in range(3)) + (int(a * 255),))
            else:
                row.append((0, 0, 0, 0))
        px.append(row)
    return px

def sample(u, v):
    # 角丸背景
    r = 0.18
    dx = max(abs(u - 0.5) - (0.5 - r) + 0.02, 0)
    dy = max(abs(v - 0.5) - (0.5 - r) + 0.02, 0)
    if math.hypot(dx, dy) > r:
        return (0, 0, 0, 0)
    col = (28, 32, 38, 255)
    # Wi-Fi 扇形（左上寄り）
    cx, cy = 0.38, 0.62
    d = math.hypot(u - cx, v - cy)
    ang = math.degrees(math.atan2(cy - v, u - cx))
    if 45 <= ang <= 135 or d < 0.06:
        if d < 0.06:
            return (46, 204, 113, 255)
        for r0, r1 in ((0.13, 0.19), (0.24, 0.30)):
            if r0 <= d <= r1:
                return (46, 204, 113, 255)
    # 棒グラフ（右側、強い順に長い横棒）
    bars = [(0.20, 0.26, 0.60, (46, 204, 113)), (0.31, 0.37, 0.48, (241, 196, 15)),
            (0.42, 0.48, 0.36, (230, 126, 34))]
    for top, bot, length, c in bars:
        x0 = 0.86 - length * 0.55
        if top <= v <= bot and x0 <= u <= 0.86 and not (u < 0.6 and v > 0.3):
            return c + (255,)
    return col

def write_png(path, px):
    n = len(px)
    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *p) for p in row) for row in px)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)

if __name__ == "__main__":
    res = sys.argv[1] if len(sys.argv) > 1 else "res"
    for name, n in SIZES.items():
        d = os.path.join(res, "mipmap-" + name)
        os.makedirs(d, exist_ok=True)
        write_png(os.path.join(d, "ic_launcher.png"), render(n))
