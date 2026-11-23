package com.z4motioncam;

/**
 * Follows the motion mask over time: how long has a person-sized (silhouette-like) group of moving
 * cells been there? In the dark the body model cannot confirm anything, so a shape that keeps
 * moving for a few seconds is what "a person" rests on. Fed at every motion analysis (about 3 per
 * second), independent of how often the person check itself runs.
 */
final class ShapeTracker {
    /** Shorter gaps (a flickering mask, a lighting settle) do not break the run. */
    static final long GAP_MS = 1_500L;
    private static final long NEVER = Long.MIN_VALUE / 2;

    private long since = NEVER;
    private long last = NEVER;

    void update(long nowMs, boolean[] mask, int gw, int gh) {
        boolean cand = mask != null && BlobAnalyzer.largest(mask, gw, gh).silhouetteLike();
        if (!cand) return;
        if (nowMs - last > GAP_MS) since = nowMs;
        last = nowMs;
    }

    /** How long the shape has been there without a break; 0 when it is not there now. */
    long sustainedMs(long nowMs) {
        return nowMs - last <= GAP_MS ? last - since : 0L;
    }

    void reset() {
        since = NEVER;
        last = NEVER;
    }
}
