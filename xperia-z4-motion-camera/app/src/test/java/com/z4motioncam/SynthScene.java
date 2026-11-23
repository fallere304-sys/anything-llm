package com.z4motioncam;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/** Synthetic NV21 frames: a textured room plus moving objects, lighting changes and sensor noise. */
final class SynthScene {
    static final class Obj {
        final int x;
        final int y;
        final int w;
        final int h;
        final int luma;
        final boolean textured;

        Obj(int x, int y, int w, int h, int luma, boolean textured) {
            this.x = x;
            this.y = y;
            this.w = w;
            this.h = h;
            this.luma = luma;
            this.textured = textured;
        }
    }

    /** Lighting and sensor state of one frame. */
    static final class Light {
        float gain = 1f;
        float gamma = 1f;
        /** Horizontal gain ramp from the left to the right edge (1 = none). */
        float rampLeft = 1f;
        float rampRight = 1f;
        /** Soft sun patch: centre, sigma (px) and peak gain. */
        float spotX;
        float spotY;
        float spotSigma = 1f;
        float spotGain = 1f;
        float noise;
        Random rnd;

        Light gain(float g) { gain = g; return this; }
        Light gamma(float g) { gamma = g; return this; }
        Light ramp(float l, float r) { rampLeft = l; rampRight = r; return this; }
        Light spot(float x, float y, float s, float g) { spotX = x; spotY = y; spotSigma = s; spotGain = g; return this; }
        Light noise(float n, long seed) { noise = n; rnd = new Random(seed); return this; }
    }

    final int w;
    final int h;
    private final float[] base;

    SynthScene(int w, int h, long seed) {
        this.w = w;
        this.h = h;
        base = new float[w * h];
        Random r = new Random(seed);
        // Smooth value noise on a coarse lattice (cells of about w/24 px) plus sharp rectangles.
        int step = Math.max(4, w / 24);
        int lw = w / step + 2;
        int lh = h / step + 2;
        float[] lat = new float[lw * lh];
        for (int i = 0; i < lat.length; i++) lat[i] = 60 + r.nextInt(120);
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                float fx = (float) x / step;
                float fy = (float) y / step;
                int ix = (int) fx;
                int iy = (int) fy;
                float ax = fx - ix;
                float ay = fy - iy;
                float v = lat[iy * lw + ix] * (1 - ax) * (1 - ay) + lat[iy * lw + ix + 1] * ax * (1 - ay)
                        + lat[(iy + 1) * lw + ix] * (1 - ax) * ay + lat[(iy + 1) * lw + ix + 1] * ax * ay;
                base[y * w + x] = v;
            }
        }
        for (int k = 0; k < 14; k++) {
            int rw = w / 12 + r.nextInt(w / 8);
            int rh = h / 12 + r.nextInt(h / 6);
            int rx = r.nextInt(w - rw);
            int ry = r.nextInt(h - rh);
            float lum = 50 + r.nextInt(150);
            for (int y = ry; y < ry + rh; y++) for (int x = rx; x < rx + rw; x++) base[y * w + x] = lum + (x + y) % 7;
        }
    }

    /** A completely flat wall instead of a textured room. */
    static SynthScene flat(int w, int h, int luma) {
        SynthScene s = new SynthScene(w, h, 1);
        java.util.Arrays.fill(s.base, luma);
        return s;
    }

    byte[] frame(Light l, List<Obj> objs) {
        byte[] f = new byte[w * h * 3 / 2];
        float[] row = new float[w];
        for (int y = 0; y < h; y++) {
            System.arraycopy(base, y * w, row, 0, w);
            for (Obj o : objs) {
                if (y < o.y || y >= o.y + o.h) continue;
                for (int x = Math.max(0, o.x); x < Math.min(w, o.x + o.w); x++) {
                    row[x] = o.textured ? o.luma + ((x / 3 + y / 3) % 2) * 40 - 20 : o.luma;
                }
            }
            for (int x = 0; x < w; x++) {
                float v = row[x] / 255f;
                if (l.gamma != 1f) v = (float) Math.pow(v, l.gamma);
                v *= l.gain * (l.rampLeft + (l.rampRight - l.rampLeft) * x / w);
                if (l.spotGain != 1f) {
                    float dx = x - l.spotX;
                    float dy = y - l.spotY;
                    v *= 1 + (l.spotGain - 1) * (float) Math.exp(-(dx * dx + dy * dy) / (2 * l.spotSigma * l.spotSigma));
                }
                v *= 255f;
                if (l.noise > 0) v += (float) (l.rnd.nextGaussian() * l.noise);
                f[y * w + x] = (byte) Math.max(0, Math.min(255, Math.round(v)));
            }
        }
        java.util.Arrays.fill(f, w * h, f.length, (byte) 128);
        return f;
    }

    /** A person-like figure (body, head) with feet at (cx, feetY) and the given height in px. */
    static List<Obj> person(int cx, int feetY, int height, int luma, boolean textured) {
        List<Obj> l = new ArrayList<>();
        int bw = Math.max(3, height * 3 / 10);
        int hh = height / 8;
        l.add(new Obj(cx - bw / 2, feetY - height + hh, bw, height - hh, luma, textured));
        l.add(new Obj(cx - hh / 2, feetY - height, Math.max(2, hh), hh, luma, textured));
        return l;
    }
}
