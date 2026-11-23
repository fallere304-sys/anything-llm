package com.z4motioncam;

/** {@link BodyFinder} on a {@link BodyRuntime}: crop, brighten, run, pick the person. */
final class ModelBodyFinder implements BodyFinder {
    /** Consecutive failed runs after which the model is given up. */
    static final int MAX_FAILURES = 3;

    private final BodyRuntime runtime;
    private final byte[] rgb = new byte[BodyInput.BYTES];
    private final BodyOutput out = new BodyOutput();
    private int failures;

    ModelBodyFinder(BodyRuntime runtime) {
        this.runtime = runtime;
    }

    @Override
    public Verdict check(byte[] nv21, int w, int h, int rot, BodyCrop crop, float minScore) {
        if (broken()) return new Verdict(Verdict.Kind.FAILED, 0f);
        float mean = BodyInput.crop(nv21, w, h, rot, crop.x, crop.y, crop.w, crop.h, rgb);
        if (mean < BodyInput.DARK_MEAN) return new Verdict(Verdict.Kind.TOO_DARK, 0f);
        boolean ok;
        try {
            out.count = 0;
            ok = runtime.run(rgb, out);
        } catch (RuntimeException | LinkageError e) {
            ok = false; // the runtime may not be loadable at all (missing .so): count it as a failure
        }
        if (!ok) {
            failures++;
            return new Verdict(Verdict.Kind.FAILED, 0f);
        }
        failures = 0;
        BodyDecoder.Hit hit = BodyDecoder.bestPerson(out, minScore, crop.region);
        return hit == null ? new Verdict(Verdict.Kind.NOT_PERSON, 0f) : new Verdict(Verdict.Kind.PERSON, hit.score);
    }

    @Override
    public boolean broken() {
        return failures >= MAX_FAILURES;
    }

    @Override
    public void close() {
        runtime.close();
    }
}
