package com.z4motioncam;

/** Runs the body model once; the TensorFlow Lite implementation lives outside the plain build. */
interface BodyRuntime {
    /** @param rgb300 {@link BodyInput#BYTES} bytes; @return false when the run failed */
    boolean run(byte[] rgb300, BodyOutput out);

    void close();
}
