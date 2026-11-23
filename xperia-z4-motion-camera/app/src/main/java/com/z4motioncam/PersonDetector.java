package com.z4motioncam;

/**
 * Decides whether a person is in a frame. It is called only after the motion detector saw
 * movement (or, while recording, every couple of seconds), never on every frame. Implementations
 * are interchangeable; see {@link HybridPersonDetector} for the one in use and README "人認識" for
 * the body model (optional, only in the PERSON_MODEL=1 build).
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
        /** Monotonic time of this check. */
        long nowMs;
        /** How long a person-sized moving shape has been there without a break (see {@link ShapeTracker}). */
        long shapeSustainedMs;
        /** The camera is recording right now (nothing may be moving then: a person sitting still). */
        boolean recording;
        ThermalPolicy.Level thermal = ThermalPolicy.Level.NORMAL;
    }

    final class Result {
        static final Result NONE = new Result(false, 0f, "");
        /** "Not asked this time" (the body model is rate-limited): the caller must not count it as a check. */
        static final Result SKIPPED = new Result(false, 0f, "");
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

    Result detect(Input in);

    void close();
}
