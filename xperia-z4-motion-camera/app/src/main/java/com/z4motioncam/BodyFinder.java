package com.z4motioncam;

/** Asks the body model whether a crop of the frame holds a person. */
interface BodyFinder {
    /** Result of one question. */
    final class Verdict {
        enum Kind { PERSON, NOT_PERSON, TOO_DARK, FAILED }

        final Kind kind;
        /** Best person score (PERSON), or 0. */
        final float score;

        Verdict(Kind kind, float score) {
            this.kind = kind;
            this.score = score;
        }
    }

    /**
     * @param nv21,w,h,rot the frame (see {@link BodyInput#crop})
     * @param crop         where to look
     * @param minScore     lowest person score that counts
     */
    Verdict check(byte[] nv21, int w, int h, int rot, BodyCrop crop, float minScore);

    /** True once the model has failed so often that it is not worth asking again. */
    boolean broken();

    void close();
}
