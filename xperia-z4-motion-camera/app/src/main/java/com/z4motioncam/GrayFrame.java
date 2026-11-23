package com.z4motioncam;

/**
 * Turns the luma plane of an NV21 frame into a small, upright RGB_565 grey picture for
 * android.media.FaceDetector (which wants an even width, RGB_565 and an upright face). Nearest
 * neighbour sampling: a few tens of thousands of reads, not a full-frame pass.
 */
final class GrayFrame {
    /** Longer side of the picture handed to the face detector. */
    static final int LONG_SIDE = 320;

    private GrayFrame() {}

    /** {width, height} of the output for a frame, after rotation; both even and at least 2. */
    static int[] outputSize(int w, int h, int rotation) {
        boolean turned = ((rotation % 360) + 360) % 360 % 180 != 0;
        int rw = turned ? h : w;
        int rh = turned ? w : h;
        int longSide = Math.min(LONG_SIDE, Math.max(rw, rh));
        int ow;
        int oh;
        if (rw >= rh) {
            ow = longSide;
            oh = Math.max(2, Math.round(longSide * (float) rh / rw));
        } else {
            oh = longSide;
            ow = Math.max(2, Math.round(longSide * (float) rw / rh));
        }
        return new int[] {Math.max(2, ow & ~1), Math.max(2, oh & ~1)};
    }

    /**
     * @param rotation clockwise degrees (0/90/180/270) that make the picture upright
     * @param dst      at least outputSize(...)[0] * [1] shorts
     */
    static void toRgb565(byte[] nv21, int w, int h, int rotation, short[] dst) {
        int rot = ((rotation % 360) + 360) % 360;
        int[] o = outputSize(w, h, rot);
        int ow = o[0];
        int oh = o[1];
        boolean turned = rot % 180 != 0;
        int rw = turned ? h : w; // size of the rotated picture in source pixels
        int rh = turned ? w : h;
        for (int oy = 0; oy < oh; oy++) {
            int v = (int) (((2L * oy + 1) * rh) / (2L * oh));
            for (int ox = 0; ox < ow; ox++) {
                int u = (int) (((2L * ox + 1) * rw) / (2L * ow));
                int sx;
                int sy;
                switch (rot) {
                    case 90: sx = v; sy = h - 1 - u; break;
                    case 180: sx = w - 1 - u; sy = h - 1 - v; break;
                    case 270: sx = w - 1 - v; sy = u; break;
                    default: sx = u; sy = v; break;
                }
                int g = nv21[sy * w + sx] & 0xFF;
                dst[oy * ow + ox] = (short) (((g >> 3) << 11) | ((g >> 2) << 5) | (g >> 3));
            }
        }
    }
}
