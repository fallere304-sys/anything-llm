package com.z4motioncam;

/**
 * Person check from two kinds of evidence, both cheap enough for a phone that must stay cool:
 * <ol>
 *   <li>A face on a 320-px grey picture (android.media.FaceDetector, behind {@link FaceFinder}).
 *       Strong evidence, but it needs a lit, big, frontal face; skipped in the dark.
 *   <li>The silhouette of what moves ({@link SilhouetteTracker}): size, proportions, a tall core,
 *       a few seconds of persistence, not a swinging curtain. It sees people from behind, small,
 *       lying down and in the dark. The three sensitivities differ in how strict this is.
 * </ol>
 */
final class HybridPersonDetector implements PersonDetector {
    /** Finds faces in an upright RGB_565 picture; returns the best confidence 0..1 (0 = none). */
    interface FaceFinder {
        float bestConfidence(short[] rgb565, int width, int height);

        void close();
    }

    private final FaceFinder faces;
    private final int sensitivity;
    private final SilhouetteTracker tracker = new SilhouetteTracker();
    private short[] buffer = new short[0];
    private SilhouetteTracker.Shape lastShape = new SilhouetteTracker.Shape();

    /** @param sensitivity 1 strict .. 3 loose (also accepts person-sized moving shapes) */
    HybridPersonDetector(FaceFinder faces, int sensitivity) {
        this.faces = faces;
        this.sensitivity = Math.max(1, Math.min(3, sensitivity));
    }

    /** Minimum FaceDetector confidence; FaceDetector reports roughly 0.4-0.6 for a clear face. */
    static float faceThreshold(int sensitivity) {
        switch (sensitivity) {
            case 1: return 0.55f;
            case 3: return 0.30f;
            default: return 0.42f;
        }
    }

    /** Mean luma (0..255) of a sparse sample of the frame. */
    static int meanLuma(byte[] nv21, int w, int h) {
        if (nv21 == null || w <= 0 || h <= 0 || nv21.length < w * h) return 128;
        long sum = 0;
        int n = 0;
        for (int y = h / 24; y < h; y += Math.max(1, h / 12)) {
            for (int x = w / 32; x < w; x += Math.max(1, w / 16)) {
                sum += nv21[y * w + x] & 0xFF;
                n++;
            }
        }
        return n == 0 ? 128 : (int) (sum / n);
    }

    /** Below this mean luma the picture counts as dark: shape limits relax, the face search is skipped. */
    static final int DARK_LUMA = 40;
    private static final int FACELESS_LUMA = 14;

    @Override
    public void observeMotion(boolean[] mask, int gridW, int gridH, int rotation, long nowMs) {
        tracker.observe(mask, gridW, gridH, rotation, nowMs);
    }

    SilhouetteTracker.Shape lastShape() {
        return lastShape;
    }

    @Override
    public Result detect(Input in) {
        if (in.motionMask != null && tracker.lastObserveMs() != in.nowMs) {
            tracker.observe(in.motionMask, in.gridW, in.gridH, in.rotation, in.nowMs);
        }
        int luma = meanLuma(in.nv21, in.width, in.height);
        float conf = 0f;
        if (luma >= FACELESS_LUMA) {
            int[] o = GrayFrame.outputSize(in.width, in.height, in.rotation);
            if (buffer.length < o[0] * o[1]) buffer = new short[o[0] * o[1]];
            GrayFrame.toRgb565(in.nv21, in.width, in.height, in.rotation, buffer);
            conf = faces.bestConfidence(buffer, o[0], o[1]);
            if (conf >= faceThreshold(sensitivity)) return new Result(true, conf, "顔");
        }
        lastShape = tracker.evaluate(in.nowMs, sensitivity, luma < DARK_LUMA);
        if (lastShape.person) return new Result(true, Math.max(0.1f, Math.min(0.5f, lastShape.score)), "形");
        return new Result(false, conf, "");
    }

    @Override
    public void close() {
        faces.close();
    }
}
