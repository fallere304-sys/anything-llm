package com.z4motioncam;

/**
 * Decides whether a person is in a frame. It is called only after the motion detector saw
 * movement (or, while recording, every couple of seconds), never on every frame. Implementations
 * are interchangeable; see {@link HybridPersonDetector} for the one in use and README "人認識" for
 * why a neural network was not bundled.
 */
interface PersonDetector {
    /** One frame plus what the motion detector already knows about it. */
    final class Input {
        byte[] nv21;
        int width;
        int height;
        /** Display rotation (0/90/180/270, clockwise): the picture is upright after rotating by it. */
        int rotation;
        /** Motion grid of this frame (changed cells), may be null. */
        boolean[] motionMask;
        int gridW;
        int gridH;
        /** Monotonic time of this frame in ms (the detector's clock for the shape history). */
        long nowMs;
    }

    final class Result {
        static final Result NONE = new Result(false, 0f, "");
        final boolean person;
        /** 0..1, comparable only within one detector. */
        final float confidence;
        /** What the decision rests on, for the journal ("顔" / "動く塊"). */
        final String basis;

        Result(boolean person, float confidence, String basis) {
            this.person = person;
            this.confidence = confidence;
            this.basis = basis;
        }
    }

    /**
     * Called after every motion analysis (also when nothing moved) so that the detector can follow
     * a shape over a few seconds. Cheap; the mask is only read.
     */
    void observeMotion(boolean[] mask, int gridW, int gridH, int rotation, long nowMs);

    Result detect(Input in);

    void close();
}
