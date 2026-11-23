package com.z4motioncam;

/**
 * Person check, cheapest evidence first. Motion (the gatekeeper, outside) has already seen something move.
 * <ol>
 *   <li>A face on a 320-px grey picture (android.media.FaceDetector, behind {@link FaceFinder}): the
 *       strongest and cheapest evidence, it also tells a person from a pet or a curtain.
 *   <li>The <em>shape</em> of what moved ({@link BlobAnalyzer}): a compact, person-sized group of
 *       changed cells, small and thin ones included (silhouettes).
 *   <li>The <em>body model</em> ({@link BodyFinder}, optional): the moving group is cut out and a
 *       neural network says whether a person is in it. Asked at most once a second, only when the
 *       crop is bright enough to see anything, never when the phone is critically hot.
 * </ol>
 * The three sensitivities (person_sens):
 * <ul>
 *   <li>1 strict: a clear face, or the body model finds a person. Nothing else.
 *   <li>2 standard: shape + model. A face counts too. When the crop is too dark for the model (or
 *       there is no model), a person-sized shape that keeps moving for a few seconds counts instead.
 *   <li>3 loose: a face or the shape alone.
 * </ul>
 * Without the model (the light build, or when it fails) 1 and 2 work on faces, and 2 also on the
 * dark-room shape rule.
 */
final class HybridPersonDetector implements PersonDetector {
    /** Finds faces in an upright RGB_565 picture; returns the best confidence 0..1 (0 = none). */
    interface FaceFinder {
        float bestConfidence(short[] rgb565, int width, int height);

        void close();
    }

    /** A shape must keep moving this long (in the dark, where the model cannot look) to count as a person. */
    static final long DARK_HOLD_MS = 3_000L;
    /** Groups smaller than this share of the picture are not worth a model run. */
    static final float MIN_CROP_AREA = 0.010f;
    static final long MODEL_INTERVAL_MS = 1_000L;

    private final FaceFinder faces;
    private final BodyFinder body;
    private final int sensitivity;
    private short[] buffer = new short[0];
    private long lastModelMs = Long.MIN_VALUE / 2;

    /** @param sensitivity 1 strict .. 3 loose (also accepts person-sized moving shapes) */
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

    /** Wait between two model runs; longer when the phone is warm, never when it is critical. */
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

    @Override
    public Result detect(Input in) {
        BlobAnalyzer.Blob blob = BlobAnalyzer.largest(in.motionMask, in.gridW, in.gridH);
        boolean shape = blob.silhouetteLike();

        // What would the model look at? Around the moving group; the whole picture when nothing moves
        // but a recording is running (a person sitting still).
        BodyCrop crop = null;
        if (sensitivity < 3) {
            if (blob.area() >= MIN_CROP_AREA && blob.area() <= BlobAnalyzer.MAX_AREA) {
                crop = BodyCrop.around(blob, in.gridW, in.gridH, in.width, in.height, in.rotation);
            } else if (in.recording && blob.cells == 0) {
                int[] r = rotated(in);
                crop = BodyCrop.whole(r[0], r[1]);
            }
        }
        boolean modelPath = crop != null && bodyUsable() && in.thermal != ThermalPolicy.Level.CRITICAL;
        if (modelPath && in.nowMs - lastModelMs < modelIntervalMs(in.thermal)) return Result.SKIPPED;

        int[] o = GrayFrame.outputSize(in.width, in.height, in.rotation);
        if (buffer.length < o[0] * o[1]) buffer = new short[o[0] * o[1]];
        GrayFrame.toRgb565(in.nv21, in.width, in.height, in.rotation, buffer);
        float conf = faces.bestConfidence(buffer, o[0], o[1]);
        if (conf >= faceThreshold(sensitivity)) return new Result(true, conf, "顔");

        if (sensitivity >= 3) {
            if (shape) return new Result(true, Math.min(0.5f, blob.area()), "動く塊");
            return new Result(false, conf, "");
        }

        if (modelPath) {
            BodyFinder.Verdict v = body.check(in.nv21, in.width, in.height, in.rotation, crop, bodyThreshold(sensitivity));
            if (v.kind != BodyFinder.Verdict.Kind.TOO_DARK) lastModelMs = in.nowMs;
            switch (v.kind) {
                case PERSON: return new Result(true, v.score, "体モデル");
                case NOT_PERSON: return new Result(false, conf, ""); // a curtain, a pet, a light: the model says no
                default: break; // too dark / failed: fall through to the shape rule
            }
        }
        if (darkShapeIsAPerson(in, crop, shape)) return new Result(true, Math.min(0.5f, blob.area()), "形（暗所）");
        return new Result(false, conf, "");
    }

    /** Standard only: a person-sized shape that keeps moving in a crop too dark to check. */
    private boolean darkShapeIsAPerson(Input in, BodyCrop crop, boolean shape) {
        if (sensitivity != 2 || !shape || crop == null || in.shapeSustainedMs < DARK_HOLD_MS) return false;
        float mean = BodyInput.regionMean(in.nv21, in.width, in.height, in.rotation, crop.x, crop.y, crop.w, crop.h);
        return mean < BodyInput.DARK_MEAN; // brighter: a face or the model's verdict stands
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
