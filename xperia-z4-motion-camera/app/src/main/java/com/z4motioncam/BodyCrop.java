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
     * Square crop around a group of cells of the <em>upright</em> grid (what {@link SilhouetteTracker}
     * reports), scaled to the upright picture.
     *
     * @param minX,minY,maxX,maxY bounding box of the group (cells, inclusive)
     * @param gw,gh                upright grid size
     * @param w,h                  frame size in pixels (sensor orientation, not rotated)
     * @param rot                  clockwise degrees that make the picture upright
     */
    static BodyCrop aroundCells(int minX, int minY, int maxX, int maxY, int gw, int gh, int w, int h, int rot) {
        rot = ((rot % 360) + 360) % 360;
        int rw = rot % 180 != 0 ? h : w;
        int rh = rot % 180 != 0 ? w : h;
        int u0 = minX * rw / gw;
        int u1 = (maxX + 1) * rw / gw;
        int v0 = minY * rh / gh;
        int v1 = (maxY + 1) * rh / gh;
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
