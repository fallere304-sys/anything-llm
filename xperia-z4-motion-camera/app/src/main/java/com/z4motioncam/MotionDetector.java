package com.z4motioncam;

import java.util.Arrays;

/**
 * Cheap frame-difference motion detector working on the luma plane of NV21 frames.
 *
 * <p>The frame is reduced to a coarse grid of cell averages (sampling every 4th pixel), which is
 * compared with a slowly adapting background. Only a few thousand pixels are read per frame, so
 * it can run a few times per second at negligible CPU cost.
 */
final class MotionDetector {
    private static final int SAMPLE_STEP = 4;
    /** Luma difference (0..255) above which a cell counts as changed. */
    private static final int CELL_THRESHOLD = 18;
    /** Consecutive analyses over the threshold required to trigger (filters sensor noise). */
    private static final int REQUIRED_HITS = 2;

    private final int gridW;
    private final int gridH;
    private final int[] cur;
    private final int[] diff;
    private final int[] hist = new int[511];
    /** Background in fixed point (x16). */
    private final int[] bg;
    private boolean initialized;
    private int hits;
    private float lastRatio;

    MotionDetector(int gridW, int gridH) {
        this.gridW = gridW;
        this.gridH = gridH;
        this.cur = new int[gridW * gridH];
        this.diff = new int[gridW * gridH];
        this.bg = new int[gridW * gridH];
    }

    /** Grid of roughly 32 columns with the frame's aspect ratio. */
    static MotionDetector forFrame(int width, int height) {
        int gw = 32;
        int gh = Math.max(1, Math.round(gw * (float) height / width));
        return new MotionDetector(gw, gh);
    }

    float lastRatio() {
        return lastRatio;
    }

    void reset() {
        initialized = false;
        hits = 0;
        lastRatio = 0f;
    }

    /**
     * Analyses one frame and returns true when motion is detected.
     *
     * @param areaThreshold fraction of cells (0..1) that must change to count as motion
     */
    boolean update(byte[] nv21, int width, int height, float areaThreshold) {
        float ratio = analyze(nv21, width, height);
        if (ratio >= areaThreshold) {
            hits++;
        } else {
            hits = 0;
        }
        return hits >= REQUIRED_HITS;
    }

    /** Returns the fraction of grid cells that differ from the background (0..1). */
    float analyze(byte[] nv21, int width, int height) {
        int cellW = width / gridW;
        int cellH = height / gridH;
        int n = gridW * gridH;
        for (int gy = 0; gy < gridH; gy++) {
            for (int gx = 0; gx < gridW; gx++) {
                int x0 = gx * cellW;
                int y0 = gy * cellH;
                int sum = 0;
                int count = 0;
                for (int y = y0 + SAMPLE_STEP / 2; y < y0 + cellH; y += SAMPLE_STEP) {
                    int row = y * width;
                    for (int x = x0 + SAMPLE_STEP / 2; x < x0 + cellW; x += SAMPLE_STEP) {
                        sum += nv21[row + x] & 0xFF;
                        count++;
                    }
                }
                int i = gy * gridW + gx;
                cur[i] = count == 0 ? 0 : sum / count;
            }
        }

        if (!initialized) {
            for (int i = 0; i < n; i++) bg[i] = cur[i] << 4;
            initialized = true;
            lastRatio = 0f;
            return 0f;
        }

        // Remove the global brightness shift (auto exposure, lights dimming) before comparing.
        // The median is used so that a large moving object does not shift the rest of the frame.
        Arrays.fill(hist, 0);
        for (int i = 0; i < n; i++) {
            diff[i] = cur[i] - (bg[i] >> 4);
            hist[diff[i] + 255]++;
        }
        int shift = 0;
        for (int b = 0, seen = 0; b < hist.length; b++) {
            seen += hist[b];
            if (seen * 2 >= n) {
                shift = b - 255;
                break;
            }
        }
        int changed = 0;
        for (int i = 0; i < n; i++) {
            int d = diff[i] - shift;
            if (d > CELL_THRESHOLD || d < -CELL_THRESHOLD) changed++;
            // Exponential moving average, alpha = 1/8.
            bg[i] += ((cur[i] << 4) - bg[i]) >> 3;
        }
        lastRatio = changed / (float) n;
        return lastRatio;
    }
}
