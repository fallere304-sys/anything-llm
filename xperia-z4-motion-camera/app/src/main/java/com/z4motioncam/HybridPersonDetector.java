package com.z4motioncam;

/**
 * Person check in the order cheapest evidence first. The motion detector (the gatekeeper, outside)
 * has already seen something move, in a brightness-invariant way.
 * <ol>
 *   <li><b>Shape</b> ({@link SilhouetteTracker}): size, proportions, a tall core, a few seconds of
 *       persistence, not a swinging curtain. It sees people from behind, small, lying down and in
 *       the dark. It is the candidate-maker: the group it finds is what the later steps look at.
 *   <li><b>Face</b> (android.media.FaceDetector on a 320-px grey picture, behind {@link FaceFinder}):
 *       cheap, strong when a lit frontal face is there; skipped in the dark.
 *   <li><b>Body model</b> ({@link BodyFinder}, optional, only in the PERSON_MODEL=1 build): the
 *       candidate is cut out and a neural network says whether a person is in it; this is what
 *       removes curtains and pets. Asked at most once a second (slower when warm, never when
 *       critically hot), only when the crop is bright enough to see anything.
 * </ol>
 * The three sensitivities (person_sens):
 * <ul>
 *   <li>1 strict: a clear face, or the model finds a person. Shape alone only where the model cannot
 *       look (no model / dark / failed), with the strictest shape limits and some seconds of motion.
 *   <li>2 standard: shape + model. A face counts too. Where the model cannot look, a person-sized
 *       shape that keeps moving for a few seconds counts instead.
 *   <li>3 loose: a face, or the shape alone (small and dark ones too). The model is not asked.
 * </ul>
 * Without the model (the light build, or after it failed) the shape rules work alone, exactly as in
 * the light build.
 */
final class HybridPersonDetector implements PersonDetector {
    /** Finds faces in an upright RGB_565 picture; returns the best confidence 0..1 (0 = none). */
    interface FaceFinder {
        float bestConfidence(short[] rgb565, int width, int height);

        void close();
    }

    /** Below this mean luma the picture counts as dark: shape limits relax, the face search is skipped. */
    static final int DARK_LUMA = 40;
    private static final int FACELESS_LUMA = 14;
    /** A shape must keep moving this long to count as a person where the model was asked but could not look. */
    static final long DARK_HOLD_MS = 3_000L;
    static final long MODEL_INTERVAL_MS = 1_000L;

    private final FaceFinder faces;
    private final BodyFinder body;
    private final int sensitivity;
    private final SilhouetteTracker tracker = new SilhouetteTracker();
    private short[] buffer = new short[0];
    private SilhouetteTracker.Shape lastShape = new SilhouetteTracker.Shape();
    private long lastModelMs = Long.MIN_VALUE / 2;
    /** The last full check had a crop to show the model: until the interval is over, ask nothing (cheap early exit). */
    private boolean cropPending;

    /** @param sensitivity 1 strict .. 3 loose */
    HybridPersonDetector(FaceFinder faces, int sensitivity) {
        this(faces, null, sensitivity);
    }

    /** @param body the body model, or null (light build / failed to load) */
    HybridPersonDetector(FaceFinder faces, BodyFinder body, int sensitivity) {
        this.faces = faces;
        this.body = body;
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

    /**
     * Minimum body-model person score. SSD scores are step-like and low for small, dark or
     * partly hidden people: a clear person scores 0.6-0.8, a silhouette in the dark 0.5-0.7.
     */
    static float bodyThreshold(int sensitivity) {
        return sensitivity <= 1 ? 0.55f : 0.40f;
    }

    /** Wait between two model runs; longer when the phone is warm. */
    static long modelIntervalMs(ThermalPolicy.Level l) {
        switch (l) {
            case NORMAL: return MODEL_INTERVAL_MS;
            case WARM: return MODEL_INTERVAL_MS * 2;
            default: return MODEL_INTERVAL_MS * 4;
        }
    }

    /** "ok", "none" (not in this build) or "failed" (could not load / keeps failing). */
    String bodyState() {
        if (body == null) return "none";
        return body.broken() ? "failed" : "ok";
    }

    boolean bodyUsable() {
        return body != null && !body.broken();
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

    @Override
    public void observeMotion(boolean[] mask, int gridW, int gridH, int rotation, long nowMs) {
        tracker.observe(mask, gridW, gridH, rotation, nowMs);
    }

    SilhouetteTracker.Shape lastShape() {
        return lastShape;
    }

    private static boolean anyMotion(boolean[] mask) {
        if (mask == null) return false;
        for (boolean b : mask) if (b) return true;
        return false;
    }

    @Override
    public Result detect(Input in) {
        if (in.motionMask != null && tracker.lastObserveMs() != in.nowMs) {
            tracker.observe(in.motionMask, in.gridW, in.gridH, in.rotation, in.nowMs);
        }
        boolean modelOk = sensitivity < 3 && bodyUsable() && in.thermal != ThermalPolicy.Level.CRITICAL;
        if (modelOk && cropPending && in.nowMs - lastModelMs < modelIntervalMs(in.thermal)) return Result.SKIPPED;
        int luma = meanLuma(in.nv21, in.width, in.height);
        boolean dark = luma < DARK_LUMA;

        // Step 1: what would the model look at? A moving group that looks like a person under the
        // loosest limits (so small, far people reach the model too); the whole picture when nothing
        // moves but a recording is running (a person sitting still). Loose never asks the model.
        BodyCrop crop = null;
        if (modelOk) {
            SilhouetteTracker.Shape cand = tracker.evaluate(in.nowMs, 3, dark);
            if (cand.person) {
                crop = BodyCrop.aroundCells(cand.minX, cand.minY, cand.maxX, cand.maxY, cand.gridW, cand.gridH,
                        in.width, in.height, in.rotation);
            } else if (in.recording && !anyMotion(in.motionMask)) {
                int[] r = rotated(in);
                crop = BodyCrop.whole(r[0], r[1]);
            }
            cropPending = crop != null;
            if (crop != null && in.nowMs - lastModelMs < modelIntervalMs(in.thermal)) return Result.SKIPPED;
        }

        // Step 2: a face.
        float conf = 0f;
        if (luma >= FACELESS_LUMA) {
            int[] o = GrayFrame.outputSize(in.width, in.height, in.rotation);
            if (buffer.length < o[0] * o[1]) buffer = new short[o[0] * o[1]];
            GrayFrame.toRgb565(in.nv21, in.width, in.height, in.rotation, buffer);
            conf = faces.bestConfidence(buffer, o[0], o[1]);
            if (conf >= faceThreshold(sensitivity)) return new Result(true, conf, "顔");
        }

        // Step 3: the body model's verdict on the candidate.
        boolean modelCouldNotLook = false;
        if (crop != null) {
            BodyFinder.Verdict v = body.check(in.nv21, in.width, in.height, in.rotation, crop, bodyThreshold(sensitivity));
            if (v.kind != BodyFinder.Verdict.Kind.TOO_DARK) lastModelMs = in.nowMs;
            switch (v.kind) {
                case PERSON: return new Result(true, v.score, "体モデル");
                case NOT_PERSON: return new Result(false, conf, ""); // a curtain, a pet, a light: the model says no
                default: modelCouldNotLook = true; break; // too dark / failed: the shape decides, after a few seconds
            }
        }

        // Step 4: the shape rules of this level (the only evidence in the light build).
        lastShape = tracker.evaluate(in.nowMs, sensitivity, dark);
        if (lastShape.person) {
            if (modelCouldNotLook && tracker.sustainedMs(in.nowMs) < DARK_HOLD_MS) return new Result(false, conf, "");
            return new Result(true, Math.max(0.1f, Math.min(0.5f, lastShape.score)), modelCouldNotLook ? "形（暗所）" : "形");
        }
        return new Result(false, conf, "");
    }

    private static int[] rotated(Input in) {
        boolean turned = ((in.rotation % 360) + 360) % 360 % 180 != 0;
        return turned ? new int[] {in.height, in.width} : new int[] {in.width, in.height};
    }

    @Override
    public void close() {
        faces.close();
        if (body != null) body.close();
    }
}
