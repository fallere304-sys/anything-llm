package com.z4motioncam;

/**
 * Input pipeline of the body model (SSD MobileNet v1, 300x300 uint8 RGB): crops an upright
 * rectangle out of an NV21 frame, resizes it by nearest neighbour, lifts dark pictures with a
 * gamma curve and converts YUV to RGB. Integer arithmetic throughout (only the gamma table uses
 * floating point), so the numbers can be reproduced exactly by tools/person-model/reference.py;
 * the unit tests pin them.
 */
final class BodyInput {
    static final int SIZE = 300;
    static final int BYTES = SIZE * SIZE * 3;
    /** Pictures with a mean luma below this are brightened (the model was trained on daylight photos). */
    static final float LIFT_BELOW = 110f;
    /** Mean luma (0..255) of the crop below which it is too dark to tell anything: the model is not asked. */
    static final float DARK_MEAN = 12f;
    static final double MIN_GAMMA = 0.40;

    private BodyInput() {}

    /** Gamma exponent (0.40..1.0, steps of 0.05) that brings a picture of this mean luma up to about {@link #LIFT_BELOW}. */
    static double gammaFor(double mean) {
        if (mean >= LIFT_BELOW) return 1.0;
        double g = Math.log(LIFT_BELOW / 255.0) / Math.log(Math.max(mean, 1.0) / 255.0);
        g = Math.floor(g * 20.0 + 0.5) / 20.0;
        return Math.min(1.0, Math.max(MIN_GAMMA, g));
    }

    /**
     * Fills {@code out} (BYTES long, R,G,B per pixel, row-major) from the crop (cx, cy, cw, ch) of the
     * upright picture (the frame turned clockwise by {@code rot}); returns the crop's mean luma
     * before the gamma lift.
     */
    static float crop(byte[] nv21, int w, int h, int rot, int cx, int cy, int cw, int ch, byte[] out) {
        rot = ((rot % 360) + 360) % 360;
        int[] us = new int[SIZE];
        int[] vs = new int[SIZE];
        for (int i = 0; i < SIZE; i++) {
            us[i] = cx + (int) (((2L * i + 1) * cw) / (2L * SIZE));
            vs[i] = cy + (int) (((2L * i + 1) * ch) / (2L * SIZE));
        }
        int[] ys = new int[SIZE * SIZE];
        int[] cuv = new int[SIZE * SIZE];
        long sum = 0;
        int k = 0;
        for (int oy = 0; oy < SIZE; oy++) {
            int v = vs[oy];
            for (int ox = 0; ox < SIZE; ox++, k++) {
                int u = us[ox];
                int sx;
                int sy;
                switch (rot) {
                    case 90: sx = v; sy = h - 1 - u; break;
                    case 180: sx = w - 1 - u; sy = h - 1 - v; break;
                    case 270: sx = w - 1 - v; sy = u; break;
                    default: sx = u; sy = v; break;
                }
                int y = nv21[sy * w + sx] & 0xFF;
                int vi = w * h + (sy >> 1) * w + (sx & ~1);
                ys[k] = y;
                cuv[k] = ((nv21[vi] & 0xFF) << 8) | (nv21[vi + 1] & 0xFF);
                sum += y;
            }
        }
        double mean = sum / (double) (SIZE * SIZE);
        double g = gammaFor(mean);
        int[] lut = new int[256];
        for (int i = 0; i < 256; i++) lut[i] = g == 1.0 ? i : (int) Math.floor(255.0 * Math.pow(i / 255.0, g) + 0.5);
        for (int i = 0, o = 0; i < SIZE * SIZE; i++) {
            int y = lut[ys[i]];
            int vv = (cuv[i] >> 8) - 128;
            int uu = (cuv[i] & 0xFF) - 128;
            out[o++] = (byte) clamp(y + ((359 * vv) >> 8));
            out[o++] = (byte) clamp(y - ((88 * uu + 183 * vv) >> 8));
            out[o++] = (byte) clamp(y + ((454 * uu) >> 8));
        }
        return (float) mean;
    }

    /** Mean luma of a crop from a coarse sample (24x24 points); cheap, used where the model is not run. */
    static float regionMean(byte[] nv21, int w, int h, int rot, int cx, int cy, int cw, int ch) {
        rot = ((rot % 360) + 360) % 360;
        final int n = 24;
        long sum = 0;
        for (int j = 0; j < n; j++) {
            int v = cy + (int) (((2L * j + 1) * ch) / (2L * n));
            for (int i = 0; i < n; i++) {
                int u = cx + (int) (((2L * i + 1) * cw) / (2L * n));
                int sx;
                int sy;
                switch (rot) {
                    case 90: sx = v; sy = h - 1 - u; break;
                    case 180: sx = w - 1 - u; sy = h - 1 - v; break;
                    case 270: sx = w - 1 - v; sy = u; break;
                    default: sx = u; sy = v; break;
                }
                sum += nv21[sy * w + sx] & 0xFF;
            }
        }
        return sum / (float) (n * n);
    }

    private static int clamp(int v) {
        return v < 0 ? 0 : Math.min(v, 255);
    }
}
