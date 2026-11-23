package com.z4motioncam;

/**
 * Where to look for a body: a square (so the person is not distorted when it is stretched to the
 * model's 300x300) around the group of moving cells, in the upright picture, plus where the group
 * lies inside it (to check that a person the model found is the thing that moved).
 */
final class BodyCrop {
    /** Crop side relative to the longer side of the moving group: some room around the person. */
    static final float MARGIN = 1.5f;
    static final int MIN_SIDE = 64;

    final int x;
    final int y;
    final int w;
    final int h;
    /** The moving group inside the crop, 0..1 (x0, y0, x1, y1). */
    final float[] region;

    BodyCrop(int x, int y, int w, int h, float[] region) {
        this.x = x;
        this.y = y;
        this.w = w;
        this.h = h;
        this.region = region;
    }

    /** The whole upright picture (a person sitting still while recording: nothing moves to crop around). */
    static BodyCrop whole(int rw, int rh) {
        return new BodyCrop(0, 0, rw, rh, null);
    }

    /**
     * Square crop around a group of grid cells of the <em>sensor</em> frame.
     *
     * @param w,h  frame size in pixels (not rotated)
     * @param rot  clockwise degrees that make the picture upright
     * @param blob the moving group (bounding box in cells of a gw x gh grid)
     */
    static BodyCrop around(BlobAnalyzer.Blob blob, int gw, int gh, int w, int h, int rot) {
        rot = ((rot % 360) + 360) % 360;
        // bounding box in sensor pixels, inclusive-exclusive
        int sx0 = blob.minX * w / gw;
        int sx1 = (blob.minX + blob.width) * w / gw;
        int sy0 = blob.minY * h / gh;
        int sy1 = (blob.minY + blob.height) * h / gh;
        int u0;
        int u1;
        int v0;
        int v1;
        int rw = rot % 180 != 0 ? h : w;
        int rh = rot % 180 != 0 ? w : h;
        switch (rot) {
            case 90: u0 = h - sy1; u1 = h - sy0; v0 = sx0; v1 = sx1; break;
            case 180: u0 = w - sx1; u1 = w - sx0; v0 = h - sy1; v1 = h - sy0; break;
            case 270: u0 = sy0; u1 = sy1; v0 = w - sx1; v1 = w - sx0; break;
            default: u0 = sx0; u1 = sx1; v0 = sy0; v1 = sy1; break;
        }
        int bw = Math.max(1, u1 - u0);
        int bh = Math.max(1, v1 - v0);
        int side = Math.round(Math.max(bw, bh) * MARGIN);
        side = Math.max(MIN_SIDE, Math.min(side, Math.min(rw, rh)));
        int cx = (u0 + u1) / 2;
        int cy = (v0 + v1) / 2;
        int x = Math.max(0, Math.min(cx - side / 2, rw - side));
        int y = Math.max(0, Math.min(cy - side / 2, rh - side));
        float[] region = {
            clamp01((u0 - x) / (float) side), clamp01((v0 - y) / (float) side),
            clamp01((u1 - x) / (float) side), clamp01((v1 - y) / (float) side)};
        return new BodyCrop(x, y, side, side, region);
    }

    private static float clamp01(float v) {
        return v < 0f ? 0f : Math.min(v, 1f);
    }
}
