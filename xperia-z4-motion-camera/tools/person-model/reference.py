#!/usr/bin/env python3
"""Reference implementation (numpy + ai-edge-litert) of the body model's input pipeline.

Java's BodyInput / BodyDecoder must produce the same numbers; the expected values in
app/src/test/java/com/z4motioncam/BodyModelFixtures.java come from `python3 reference.py fixtures`.

Spec (all integer arithmetic except the gamma table):
  * input: NV21 frame (w x h), clockwise display rotation `rot`, upright crop rectangle (cx, cy, cw, ch)
  * output pixel (ox, oy), 0 <= ox, oy < 300:
        u = cx + ((2*ox + 1) * cw) // 600        v = cy + ((2*oy + 1) * ch) // 600
        rot 0: sx=u, sy=v    90: sx=v, sy=h-1-u    180: sx=w-1-u, sy=h-1-v    270: sx=w-1-v, sy=u
        Y = nv21[sy*w+sx]; V = nv21[w*h + (sy>>1)*w + (sx&~1)]; U = next byte      (nearest neighbour)
  * mean = sum(Y) / 90000. If mean < 110: gamma g = clamp(round20(ln(110/255)/ln(max(mean,1)/255)), 0.40, 1.0),
    Y' = floor(255*(Y/255)^g + 0.5); otherwise Y' = Y.
  * RGB: R = Y' + ((359*(V-128))>>8); G = Y' - ((88*(U-128) + 183*(V-128))>>8); B = Y' + ((454*(U-128))>>8), clamped
  * the uint8 RGB (HWC) goes to SSD MobileNet v1 (quantized, 300x300) as is.
"""
import json
import math
import sys

import numpy as np

SIZE = 300
LIFT_BELOW = 110.0
DARK_MEAN = 12.0


def synth_nv21(w, h):
    """Deterministic test frame; the same formula is in BodyModelFixtures.synthNv21 (Java)."""
    y, x = np.mgrid[0:h, 0:w]
    Y = ((x * 5 + y * 3 + ((x ^ y) & 31) * 4 + ((x * y) >> 6)) & 255).astype(np.uint8)
    cy, cx = np.mgrid[0:h // 2, 0:w // 2]
    U = ((cx * 3 + cy * 7 + 40) & 255).astype(np.uint8)
    V = ((cx * 11 + cy * 5 + 90) & 255).astype(np.uint8)
    out = np.empty(w * h * 3 // 2, np.uint8)
    out[: w * h] = Y.ravel()
    vu = np.empty((h // 2, w), np.uint8)
    vu[:, 0::2] = V
    vu[:, 1::2] = U
    out[w * h:] = vu.ravel()
    return out


def rgb_to_nv21(rgb):
    """Camera-like NV21 from an RGB picture (BT.601 full range, 4:2:0 box average)."""
    h, w = rgb.shape[:2]
    h -= h % 2
    w -= w % 2
    f = rgb[:h, :w].astype(np.float64)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    Y = np.clip(np.floor(0.299 * r + 0.587 * g + 0.114 * b + 0.5), 0, 255).astype(np.uint8)
    u = -0.168736 * r - 0.331264 * g + 0.5 * b + 128
    v = 0.5 * r - 0.418688 * g - 0.081312 * b + 128
    u = u.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    v = v.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    U = np.clip(np.floor(u + 0.5), 0, 255).astype(np.uint8)
    V = np.clip(np.floor(v + 0.5), 0, 255).astype(np.uint8)
    out = np.empty(w * h * 3 // 2, np.uint8)
    out[: w * h] = Y.ravel()
    vu = np.empty((h // 2, w), np.uint8)
    vu[:, 0::2] = V
    vu[:, 1::2] = U
    out[w * h:] = vu.ravel()
    return out, w, h


def gamma_for(mean):
    if mean >= LIFT_BELOW:
        return 1.0
    g = math.log(LIFT_BELOW / 255.0) / math.log(max(mean, 1.0) / 255.0)
    g = math.floor(g * 20.0 + 0.5) / 20.0
    return min(1.0, max(0.40, g))


def crop_rgb(nv21, w, h, rot, cx, cy, cw, ch, lift=True):
    """Returns (rgb uint8 [300,300,3], mean luma before the lift)."""
    ox = np.arange(SIZE, dtype=np.int64)
    u = cx + ((2 * ox + 1) * cw) // (2 * SIZE)
    v = cy + ((2 * ox + 1) * ch) // (2 * SIZE)
    U_, V_ = np.meshgrid(u, v)  # [oy, ox]
    if rot == 0:
        sx, sy = U_, V_
    elif rot == 90:
        sx, sy = V_, h - 1 - U_
    elif rot == 180:
        sx, sy = w - 1 - U_, h - 1 - V_
    else:
        sx, sy = w - 1 - V_, U_
    Y = nv21[sy * w + sx].astype(np.int64)
    vi = w * h + (sy >> 1) * w + (sx & ~1)
    Vc = nv21[vi].astype(np.int64) - 128
    Uc = nv21[vi + 1].astype(np.int64) - 128
    mean = Y.sum() / float(SIZE * SIZE)
    if lift:
        g = gamma_for(mean)
        if g != 1.0:
            lut = np.floor(255.0 * (np.arange(256) / 255.0) ** g + 0.5).astype(np.int64)
            Y = lut[Y]
    R = Y + ((359 * Vc) >> 8)
    G = Y - ((88 * Uc + 183 * Vc) >> 8)
    B = Y + ((454 * Uc) >> 8)
    rgb = np.clip(np.stack([R, G, B], axis=-1), 0, 255).astype(np.uint8)
    return rgb, mean


_interp = None


def run_model(rgb, model="detect.tflite"):
    global _interp
    if _interp is None:
        from ai_edge_litert.interpreter import Interpreter
        _interp = Interpreter(model_path=model)
        _interp.allocate_tensors()
    it = _interp
    it.set_tensor(it.get_input_details()[0]["index"], rgb[None])
    it.invoke()
    o = [it.get_tensor(d["index"])[0] for d in it.get_output_details()]
    boxes, classes, scores, count = o
    return boxes.astype(np.float32), classes.astype(np.float32), scores.astype(np.float32), int(count)


def checksum(a):
    a = a.astype(np.int64).ravel()
    return int(a.sum()), int((a * (np.arange(a.size) % 251 + 1)).sum() % 1000000007)


def darken(nv, w, h, shift):
    """Same frame, luma divided by 2**shift (a dark room); chroma untouched."""
    out = nv.copy()
    out[: w * h] >>= shift
    return out


def fixtures(model):
    """Expected values for BodyModelFixtures.java: crops of synthetic frames and the model's answer to them."""
    out = {"crops": {}, "gamma": {}, "model": {}}
    for mean in (1.0, 5.0, 11.9, 12.0, 30.0, 60.0, 100.0, 109.9, 110.0, 200.0):
        out["gamma"]["%g" % mean] = gamma_for(mean)
    w, h = 160, 120
    base = synth_nv21(w, h)
    for dark in (0, 3, 5):
        nv = darken(base, w, h, dark) if dark else base
        for rot in (0, 90, 180, 270):
            rw, rh = (h, w) if rot % 180 else (w, h)
            side = min(rw, rh) - 10
            rects = {"full": (0, 0, rw, rh), "square": ((rw - side) // 2, 5, side, side),
                     "narrow": (rw // 3, 3, rw // 4, rh - 6)}
            for name, (cx, cy, cw, ch) in rects.items():
                rgb, mean = crop_rgb(nv, w, h, rot, cx, cy, cw, ch)
                pts = [[int(v) for v in rgb[py, px]] for py, px in [(0, 0), (0, 299), (299, 0), (299, 299), (150, 150), (37, 211), (222, 64)]]
                key = "d%d_r%d_%s" % (dark, rot, name)
                out["crops"][key] = {"mean": mean, "sum": checksum(rgb)[0], "wsum": checksum(rgb)[1], "pts": pts}
                if model and dark == 0 and name == "square":
                    b, c, sc, n = run_model(rgb, model)
                    out["model"][key] = {"count": n, "boxes": [round(float(x), 6) for x in b.ravel()],
                                         "classes": [float(x) for x in c], "scores": [round(float(x), 6) for x in sc]}
    return out


_interp = None


def run_model(rgb, model="detect.tflite"):
    global _interp
    if _interp is None:
        from ai_edge_litert.interpreter import Interpreter
        _interp = Interpreter(model_path=model)
        _interp.allocate_tensors()
    it = _interp
    it.set_tensor(it.get_input_details()[0]["index"], rgb[None])
    it.invoke()
    o = [it.get_tensor(d["index"])[0] for d in it.get_output_details()]
    boxes, classes, scores, count = o
    return boxes.astype(np.float32), classes.astype(np.float32), scores.astype(np.float32), int(count)


def persons(o, thr=0.0):
    b, c, s, n = o
    return [(round(float(s[i]), 4), [round(float(v), 3) for v in b[i]]) for i in range(n) if int(c[i]) == 0 and s[i] >= thr]


if __name__ == "__main__":
    # fixtures [model]                        -> JSON for BodyModelFixtures.java
    # raw <rgb-image> <rot> cx cy cw ch [model]  -> upload a photo as NV21, crop, run, print the person scores
    # dump <rgb-image> <out.nv21> <out.rgb> ...  -> the NV21 bytes and the reference 300x300 RGB bytes (for the Java comparison)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
    if cmd == "fixtures":
        print(json.dumps(fixtures(sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
    elif cmd in ("raw", "dump"):
        import cv2
        img = cv2.cvtColor(cv2.imread(sys.argv[2]), cv2.COLOR_BGR2RGB)
        # the camera delivers 640x480 (or so); resize like the preview would
        img = cv2.resize(img, (640, 480), interpolation=cv2.INTER_AREA)
        nv, w, h = rgb_to_nv21(img)
        if cmd == "raw":
            rot, cx, cy, cw, ch = [int(v) for v in sys.argv[3:8]]
            rgb, mean = crop_rgb(nv, w, h, rot, cx, cy, cw, ch)
            print("mean luma %.1f gamma %.2f" % (mean, gamma_for(mean)))
            print(persons(run_model(rgb, sys.argv[8])))
        else:
            rot, cx, cy, cw, ch = [int(v) for v in sys.argv[5:10]]
            rgb, mean = crop_rgb(nv, w, h, rot, cx, cy, cw, ch)
            nv.tofile(sys.argv[3])
            rgb.tofile(sys.argv[4])
            print("mean", mean)
