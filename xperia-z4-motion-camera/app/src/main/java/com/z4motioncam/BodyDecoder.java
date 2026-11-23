package com.z4motioncam;

/** Picks the person out of the SSD model's output (boxes, classes, scores are already decoded and NMS-filtered). */
final class BodyDecoder {
    static final int PERSON_CLASS = 0;
    /** A real person's box is not a speck: at least this share of the picture's height. */
    static final float MIN_BOX_HEIGHT = 0.12f;
    /** The centre of the person box must lie within the moving group, widened by this (share of the crop). */
    static final float REGION_SLACK = 0.15f;

    static final class Hit {
        final float score;
        final float ymin;
        final float xmin;
        final float ymax;
        final float xmax;

        Hit(float score, float ymin, float xmin, float ymax, float xmax) {
            this.score = score;
            this.ymin = ymin;
            this.xmin = xmin;
            this.ymax = ymax;
            this.xmax = xmax;
        }
    }

    private BodyDecoder() {}

    /**
     * Best person detection at or above {@code minScore}, or null.
     *
     * @param region (x0, y0, x1, y1) of the moving group in the picture, 0..1; null = anywhere
     */
    static Hit bestPerson(BodyOutput o, float minScore, float[] region) {
        Hit best = null;
        int n = Math.max(0, Math.min(o.count, BodyOutput.MAX));
        for (int i = 0; i < n; i++) {
            float s = o.scores[i];
            if (!(s >= minScore) || Math.round(o.classes[i]) != PERSON_CLASS) continue;
            float ymin = clamp01(o.boxes[i * 4]);
            float xmin = clamp01(o.boxes[i * 4 + 1]);
            float ymax = clamp01(o.boxes[i * 4 + 2]);
            float xmax = clamp01(o.boxes[i * 4 + 3]);
            if (ymax - ymin < MIN_BOX_HEIGHT || xmax <= xmin) continue;
            if (region != null) {
                float cx = (xmin + xmax) / 2f;
                float cy = (ymin + ymax) / 2f;
                if (cx < region[0] - REGION_SLACK || cx > region[2] + REGION_SLACK
                        || cy < region[1] - REGION_SLACK || cy > region[3] + REGION_SLACK) continue;
            }
            if (best == null || s > best.score) best = new Hit(s, ymin, xmin, ymax, xmax);
        }
        return best;
    }

    private static float clamp01(float v) {
        return v < 0f ? 0f : (v > 1f ? 1f : v);
    }
}
