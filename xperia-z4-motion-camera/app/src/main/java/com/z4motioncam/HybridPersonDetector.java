package com.z4motioncam;

/**
 * Person check in two steps, both cheap enough for a phone that must stay cool:
 * <ol>
 *   <li>A face on a 320-px grey picture (android.media.FaceDetector, behind {@link FaceFinder}).
 *       This is the strong evidence: it is what separates a person from a pet, a curtain or a light.
 *   <li>Only at the highest sensitivity, additionally a compact, person-sized group of moving
 *       cells ({@link BlobAnalyzer}), for people seen from behind, lying down or in the dark. It
 *       cannot tell a person from a large pet, so it is opt-in.
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
    private short[] buffer = new short[0];

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

    @Override
    public Result detect(Input in) {
        int[] o = GrayFrame.outputSize(in.width, in.height, in.rotation);
        if (buffer.length < o[0] * o[1]) buffer = new short[o[0] * o[1]];
        GrayFrame.toRgb565(in.nv21, in.width, in.height, in.rotation, buffer);
        float conf = faces.bestConfidence(buffer, o[0], o[1]);
        if (conf >= faceThreshold(sensitivity)) return new Result(true, conf, "顔");
        if (sensitivity >= 3) {
            BlobAnalyzer.Blob b = BlobAnalyzer.largest(in.motionMask, in.gridW, in.gridH);
            if (b.personLike()) return new Result(true, Math.min(0.5f, b.area()), "動く塊");
        }
        return new Result(false, conf, "");
    }

    @Override
    public void close() {
        faces.close();
    }
}
