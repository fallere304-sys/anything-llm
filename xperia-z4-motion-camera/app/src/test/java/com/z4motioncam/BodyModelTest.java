package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import org.junit.Test;

/** The body model's input pipeline, decoder, finder and its place in HybridPersonDetector (all without TensorFlow). */
public class BodyModelTest {
    private static final int GW = 32;
    private static final int GH = 24;

    /** Same formula as tools/person-model/reference.py synth_nv21. */
    static byte[] synthNv21(int w, int h) {
        byte[] out = new byte[w * h * 3 / 2];
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) out[y * w + x] = (byte) (x * 5 + y * 3 + ((x ^ y) & 31) * 4 + ((x * y) >> 6));
        for (int cy = 0; cy < h / 2; cy++)
            for (int cx = 0; cx < w / 2; cx++) {
                out[w * h + cy * w + cx * 2] = (byte) (cx * 11 + cy * 5 + 90);
                out[w * h + cy * w + cx * 2 + 1] = (byte) (cx * 3 + cy * 7 + 40);
            }
        return out;
    }

    private static byte[] darken(byte[] nv, int w, int h, int shift) {
        byte[] o = nv.clone();
        for (int i = 0; i < w * h; i++) o[i] = (byte) ((o[i] & 0xFF) >> shift);
        return o;
    }

    private static long[] checksum(byte[] a) {
        long sum = 0;
        long wsum = 0;
        for (int i = 0; i < a.length; i++) {
            int v = a[i] & 0xFF;
            sum += v;
            wsum += (long) v * (i % 251 + 1);
        }
        return new long[] {sum, wsum % 1000000007L};
    }

    // ---- input pipeline against the Python reference ----

    @Test
    public void gammaMatchesReference() {
        for (double[] g : BodyModelFixtures.GAMMA) assertEquals("mean " + g[0], g[1], BodyInput.gammaFor(g[0]), 1e-12);
    }

    @Test
    public void cropMatchesReference() {
        final int w = 160;
        final int h = 120;
        byte[] base = synthNv21(w, h);
        byte[] out = new byte[BodyInput.BYTES];
        for (BodyModelFixtures.Crop c : BodyModelFixtures.CROPS) {
            String[] k = c.key.split("_");
            int dark = Integer.parseInt(k[0].substring(1));
            int rot = Integer.parseInt(k[1].substring(1));
            int rw = rot % 180 != 0 ? h : w;
            int rh = rot % 180 != 0 ? w : h;
            int side = Math.min(rw, rh) - 10;
            int[] r;
            switch (k[2]) {
                case "full": r = new int[] {0, 0, rw, rh}; break;
                case "square": r = new int[] {(rw - side) / 2, 5, side, side}; break;
                default: r = new int[] {rw / 3, 3, rw / 4, rh - 6}; break;
            }
            byte[] nv = dark == 0 ? base : darken(base, w, h, dark);
            float mean = BodyInput.crop(nv, w, h, rot, r[0], r[1], r[2], r[3], out);
            assertEquals(c.key + " mean", c.mean, mean, 1e-3);
            long[] cs = checksum(out);
            assertEquals(c.key + " sum", c.sum, cs[0]);
            assertEquals(c.key + " wsum", c.wsum, cs[1]);
            int[][] pts = {{0, 0}, {0, 299}, {299, 0}, {299, 299}, {150, 150}, {37, 211}, {222, 64}};
            for (int i = 0; i < pts.length; i++) {
                int o = (pts[i][0] * 300 + pts[i][1]) * 3;
                for (int ch = 0; ch < 3; ch++)
                    assertEquals(c.key + " pt" + i, c.pts[i * 3 + ch], out[o + ch] & 0xFF);
            }
        }
    }

    @Test
    public void darkCropIsLiftedBrightCropIsNot() {
        final int w = 160;
        final int h = 120;
        byte[] nv = synthNv21(w, h);
        byte[] dark = darken(nv, w, h, 5);
        byte[] a = new byte[BodyInput.BYTES];
        byte[] b = new byte[BodyInput.BYTES];
        float mb = BodyInput.crop(nv, w, h, 0, 0, 0, w, h, a);
        float md = BodyInput.crop(dark, w, h, 0, 0, 0, w, h, b);
        assertTrue(mb >= BodyInput.LIFT_BELOW && md < BodyInput.DARK_MEAN);
        long lifted = checksum(b)[0];
        assertTrue("the lift must raise a dark picture clearly", lifted > checksum(a)[0] / 4);
        assertEquals(BodyInput.BYTES, a.length);
    }

    @Test
    public void regionMeanTracksTheCropMean() {
        byte[] nv = synthNv21(160, 120);
        byte[] out = new byte[BodyInput.BYTES];
        float exact = BodyInput.crop(nv, 160, 120, 90, 10, 10, 90, 90, out);
        assertEquals(exact, BodyInput.regionMean(nv, 160, 120, 90, 10, 10, 90, 90), 12f);
    }

    // ---- decoder ----

    private static BodyOutput out(float[][] boxesClassScore) {
        BodyOutput o = new BodyOutput();
        o.count = boxesClassScore.length;
        for (int i = 0; i < o.count; i++) {
            float[] r = boxesClassScore[i];
            System.arraycopy(r, 0, o.boxes, i * 4, 4);
            o.classes[i] = r[4];
            o.scores[i] = r[5];
        }
        return o;
    }

    @Test
    public void decoderKeepsOnlyPersonClassAboveThreshold() {
        BodyOutput o = out(new float[][] {
            {0.1f, 0.1f, 0.9f, 0.5f, 16f, 0.95f}, // a bird, higher score: ignored
            {0.1f, 0.4f, 0.9f, 0.8f, 0f, 0.62f},
            {0.2f, 0.1f, 0.9f, 0.3f, 0f, 0.45f}});
        BodyDecoder.Hit h = BodyDecoder.bestPerson(o, 0.5f, null);
        assertNotNull(h);
        assertEquals(0.62f, h.score, 1e-6);
        assertNull(BodyDecoder.bestPerson(o, 0.7f, null));
        assertEquals(0.62f, BodyDecoder.bestPerson(o, 0.4f, null).score, 1e-6);
    }

    @Test
    public void decoderRejectsTinyBoxesAndOnesOutsideTheMovingGroup() {
        BodyOutput tiny = out(new float[][] {{0.5f, 0.5f, 0.55f, 0.52f, 0f, 0.9f}});
        assertNull(BodyDecoder.bestPerson(tiny, 0.4f, null));
        BodyOutput far = out(new float[][] {{0.1f, 0.0f, 0.9f, 0.1f, 0f, 0.9f}}); // centre x 0.05
        float[] region = {0.6f, 0.2f, 0.9f, 0.9f};
        assertNull(BodyDecoder.bestPerson(far, 0.4f, region));
        assertNotNull(BodyDecoder.bestPerson(far, 0.4f, null));
        BodyOutput near = out(new float[][] {{0.1f, 0.5f, 0.9f, 0.8f, 0f, 0.9f}});
        assertNotNull(BodyDecoder.bestPerson(near, 0.4f, region));
    }

    @Test
    public void decoderClampsOutOfRangeBoxesAndCount() {
        BodyOutput o = out(new float[][] {{-0.2f, -0.1f, 1.3f, 0.9f, 0f, 0.8f}});
        o.count = 99; // a corrupt count must not read past the arrays
        o.scores[1] = 0.9f; // slot 1 holds class 0.0 with a box of zeros: too small, ignored
        BodyDecoder.Hit h = BodyDecoder.bestPerson(o, 0.5f, null);
        assertNotNull(h);
        assertEquals(0f, h.ymin, 0f);
        assertEquals(1f, h.ymax, 0f);
        assertEquals(0f, h.xmin, 0f);
    }

    @Test
    public void decoderHandlesRealModelAnswersForSyntheticFrames() {
        // The real model, fed the synthetic crops, sees no person (it is noise): nothing may be decoded from them.
        for (BodyModelFixtures.Answer a : BodyModelFixtures.ANSWERS) {
            BodyOutput o = new BodyOutput();
            o.count = a.count;
            System.arraycopy(a.boxes, 0, o.boxes, 0, a.boxes.length);
            System.arraycopy(a.classes, 0, o.classes, 0, a.classes.length);
            System.arraycopy(a.scores, 0, o.scores, 0, a.scores.length);
            for (int i = 0; i < o.count; i++) {
                boolean person = Math.round(o.classes[i]) == 0 && o.scores[i] >= 0.4f && o.boxes[i * 4 + 2] - o.boxes[i * 4] >= BodyDecoder.MIN_BOX_HEIGHT;
                BodyOutput one = new BodyOutput();
                one.count = 1;
                System.arraycopy(o.boxes, i * 4, one.boxes, 0, 4);
                one.classes[0] = o.classes[i];
                one.scores[0] = o.scores[i];
                assertEquals(a.key + "#" + i, person, BodyDecoder.bestPerson(one, 0.4f, null) != null);
            }
        }
    }

    /**
     * Person scores the real SSD model (reference.py + ai-edge-litert, same crop and lift) gave on two
     * photos; only the numbers are kept here: 0.70 (backlit person, whole frame), 0.73 (dark doorway
     * silhouette, tight crop with the gamma lift). They must pass the standard threshold; a typical
     * "chair" score of 0.3 must not.
     */
    @Test
    public void thresholdsFitMeasuredScores() {
        assertTrue(0.6992f >= HybridPersonDetector.bodyThreshold(1));
        assertTrue(0.7305f >= HybridPersonDetector.bodyThreshold(1));
        assertTrue(0.4258f >= HybridPersonDetector.bodyThreshold(2));
        assertTrue(0.4258f < HybridPersonDetector.bodyThreshold(1));
        assertTrue(0.33f < HybridPersonDetector.bodyThreshold(2));
    }

    // ---- BodyCrop ----

    @Test
    public void cropAroundStaysInsideTheUprightPicture() {
        BlobAnalyzer.Blob b = BlobAnalyzer.largest(mask(26, 2, 6, 18), GW, GH);
        for (int rot : new int[] {0, 90, 180, 270}) {
            BodyCrop c = BodyCrop.around(b, GW, GH, 640, 480, rot);
            int rw = rot % 180 != 0 ? 480 : 640;
            int rh = rot % 180 != 0 ? 640 : 480;
            assertTrue(c.x >= 0 && c.y >= 0 && c.x + c.w <= rw && c.y + c.h <= rh);
            assertEquals(c.w, c.h);
            assertTrue(c.region[0] >= 0 && c.region[2] <= 1 && c.region[0] < c.region[2] && c.region[1] < c.region[3]);
        }
        // rot 0: the group sits at the right edge, so the crop hugs the right edge and the group is at its right
        BodyCrop c0 = BodyCrop.around(b, GW, GH, 640, 480, 0);
        assertEquals(640, c0.x + c0.w);
        assertTrue(c0.region[2] > 0.95f);
    }

    @Test
    public void wholeCoversEverything() {
        BodyCrop c = BodyCrop.whole(480, 640);
        assertEquals(0, c.x);
        assertEquals(480, c.w);
        assertEquals(640, c.h);
        assertNull(c.region);
    }

    // ---- ModelBodyFinder ----

    private static final class FakeRuntime implements BodyRuntime {
        BodyOutput answer = new BodyOutput();
        boolean fail;
        boolean boom;
        int runs;
        boolean closed;

        @Override
        public boolean run(byte[] rgb300, BodyOutput o) {
            runs++;
            if (boom) throw new IllegalStateException("native failure");
            if (fail) return false;
            o.count = answer.count;
            System.arraycopy(answer.boxes, 0, o.boxes, 0, o.boxes.length);
            System.arraycopy(answer.classes, 0, o.classes, 0, o.classes.length);
            System.arraycopy(answer.scores, 0, o.scores, 0, o.scores.length);
            return true;
        }

        @Override
        public void close() {
            closed = true;
        }
    }

    private static byte[] flat(int w, int h, int luma) {
        byte[] n = new byte[w * h * 3 / 2];
        Arrays.fill(n, 0, w * h, (byte) luma);
        Arrays.fill(n, w * h, n.length, (byte) 128);
        return n;
    }

    private static FakeRuntime personRuntime(float score) {
        FakeRuntime r = new FakeRuntime();
        r.answer = out(new float[][] {{0.1f, 0.2f, 0.95f, 0.7f, 0f, score}});
        return r;
    }

    @Test
    public void finderSaysPersonNotPersonTooDarkFailed() {
        byte[] bright = flat(160, 120, 140);
        BodyCrop crop = BodyCrop.whole(160, 120);
        FakeRuntime rt = personRuntime(0.8f);
        ModelBodyFinder f = new ModelBodyFinder(rt);
        BodyFinder.Verdict v = f.check(bright, 160, 120, 0, crop, 0.5f);
        assertEquals(BodyFinder.Verdict.Kind.PERSON, v.kind);
        assertEquals(0.8f, v.score, 1e-6);
        assertEquals(BodyFinder.Verdict.Kind.NOT_PERSON, f.check(bright, 160, 120, 0, crop, 0.9f).kind);
        int runs = rt.runs;
        assertEquals(BodyFinder.Verdict.Kind.TOO_DARK, f.check(flat(160, 120, 5), 160, 120, 0, crop, 0.5f).kind);
        assertEquals("a dark crop is not shown to the model", runs, rt.runs);
        rt.fail = true;
        assertEquals(BodyFinder.Verdict.Kind.FAILED, f.check(bright, 160, 120, 0, crop, 0.5f).kind);
    }

    @Test
    public void finderGivesUpAfterRepeatedFailuresAndSurvivesNativeErrors() {
        FakeRuntime rt = personRuntime(0.8f);
        rt.boom = true;
        ModelBodyFinder f = new ModelBodyFinder(rt);
        byte[] bright = flat(160, 120, 140);
        for (int i = 0; i < ModelBodyFinder.MAX_FAILURES; i++) {
            assertFalse(f.broken());
            assertEquals(BodyFinder.Verdict.Kind.FAILED, f.check(bright, 160, 120, 0, BodyCrop.whole(160, 120), 0.5f).kind);
        }
        assertTrue(f.broken());
        int runs = rt.runs;
        f.check(bright, 160, 120, 0, BodyCrop.whole(160, 120), 0.5f);
        assertEquals("a broken model is not run again", runs, rt.runs);
        f.close();
        assertTrue(rt.closed);
    }

    @Test
    public void aSuccessfulRunResetsTheFailureCount() {
        FakeRuntime rt = personRuntime(0.8f);
        ModelBodyFinder f = new ModelBodyFinder(rt);
        byte[] bright = flat(160, 120, 140);
        for (int round = 0; round < 5; round++) {
            rt.fail = true;
            f.check(bright, 160, 120, 0, BodyCrop.whole(160, 120), 0.5f);
            f.check(bright, 160, 120, 0, BodyCrop.whole(160, 120), 0.5f);
            rt.fail = false;
            f.check(bright, 160, 120, 0, BodyCrop.whole(160, 120), 0.5f);
        }
        assertFalse(f.broken());
    }

    // ---- ShapeTracker ----

    private static boolean[] mask(int x0, int y0, int w, int h) {
        boolean[] m = new boolean[GW * GH];
        for (int y = y0; y < y0 + h; y++) for (int x = x0; x < x0 + w; x++) m[y * GW + x] = true;
        return m;
    }

    @Test
    public void shapeTrackerMeasuresUnbrokenPresence() {
        ShapeTracker t = new ShapeTracker();
        boolean[] person = mask(10, 6, 5, 12);
        assertEquals(0L, t.sustainedMs(0));
        for (long ms = 1000; ms <= 4000; ms += 333) t.update(ms, person, GW, GH);
        assertTrue(t.sustainedMs(4000) >= 2900);
        t.update(7000, null, GW, GH); // a long pause breaks the run
        t.update(7300, person, GW, GH);
        assertEquals(0L, t.sustainedMs(7300));
        assertEquals(0L, t.sustainedMs(20_000));
        t.reset();
        assertEquals(0L, t.sustainedMs(7300));
    }

    // ---- HybridPersonDetector with a body model ----

    private static final class Faces implements HybridPersonDetector.FaceFinder {
        float conf;

        @Override
        public float bestConfidence(short[] rgb565, int width, int height) {
            return conf;
        }

        @Override
        public void close() {}
    }

    private static final class Finder implements BodyFinder {
        Verdict verdict = new Verdict(Verdict.Kind.PERSON, 0.8f);
        int calls;
        float lastMin;
        boolean broken;
        boolean closed;

        @Override
        public Verdict check(byte[] nv21, int w, int h, int rot, BodyCrop crop, float minScore) {
            calls++;
            lastMin = minScore;
            return verdict;
        }

        @Override
        public boolean broken() {
            return broken;
        }

        @Override
        public void close() {
            closed = true;
        }
    }

    private static PersonDetector.Input in(boolean[] m, long now) {
        PersonDetector.Input i = new PersonDetector.Input();
        i.nv21 = flat(640, 480, 120);
        i.width = 640;
        i.height = 480;
        i.rotation = 90;
        i.motionMask = m;
        i.gridW = GW;
        i.gridH = GH;
        i.nowMs = now;
        return i;
    }

    private static final boolean[] BODY = mask(12, 4, 8, 14);

    @Test
    public void strictAsksTheModelAndTrustsOnlyIt() {
        Finder b = new Finder();
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 1);
        PersonDetector.Result r = d.detect(in(BODY, 10_000));
        assertTrue(r.person);
        assertEquals("体モデル", r.basis);
        assertEquals(0.55f, b.lastMin, 0f);
        b.verdict = new BodyFinder.Verdict(BodyFinder.Verdict.Kind.NOT_PERSON, 0f);
        assertFalse(d.detect(in(BODY, 20_000)).person);
        // strict never uses the dark-shape rule
        b.verdict = new BodyFinder.Verdict(BodyFinder.Verdict.Kind.TOO_DARK, 0f);
        PersonDetector.Input dark = in(BODY, 30_000);
        dark.shapeSustainedMs = 60_000;
        assertFalse(d.detect(dark).person);
    }

    @Test
    public void standardModelNoOverridesShapeAndModelYesCountsWithoutFace() {
        Finder b = new Finder();
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 2);
        assertTrue(d.detect(in(BODY, 10_000)).person);
        b.verdict = new BodyFinder.Verdict(BodyFinder.Verdict.Kind.NOT_PERSON, 0f);
        PersonDetector.Input in = in(BODY, 20_000);
        in.shapeSustainedMs = 60_000;
        assertFalse("a curtain the model rejects is not a person even if it moves long", d.detect(in).person);
        assertEquals(0.40f, b.lastMin, 0f);
    }

    @Test
    public void standardDarkShapeNeedsToKeepMoving() {
        Finder b = new Finder();
        b.verdict = new BodyFinder.Verdict(BodyFinder.Verdict.Kind.TOO_DARK, 0f);
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 2);
        PersonDetector.Input in = in(BODY, 10_000);
        in.nv21 = flat(640, 480, 4); // dark room
        in.shapeSustainedMs = 1_000;
        assertFalse(d.detect(in).person);
        in = in(BODY, 20_000);
        in.nv21 = flat(640, 480, 4);
        in.shapeSustainedMs = HybridPersonDetector.DARK_HOLD_MS;
        PersonDetector.Result r = d.detect(in);
        assertTrue(r.person);
        assertEquals("形（暗所）", r.basis);
    }

    @Test
    public void modelFailureFallsBackToShapeRuleAndFaces() {
        Finder b = new Finder();
        b.verdict = new BodyFinder.Verdict(BodyFinder.Verdict.Kind.FAILED, 0f);
        Faces f = new Faces();
        HybridPersonDetector d = new HybridPersonDetector(f, b, 2);
        PersonDetector.Input in = in(BODY, 10_000);
        in.nv21 = flat(640, 480, 4);
        in.shapeSustainedMs = 5_000;
        assertTrue(d.detect(in).person);
        f.conf = 0.9f;
        assertEquals("顔", d.detect(in(BODY, 20_000)).basis);
        b.broken = true;
        assertEquals("failed", d.bodyState());
        f.conf = 0f;
        int calls = b.calls;
        assertFalse(d.detect(in(BODY, 30_000)).person);
        assertEquals("a broken model is not asked", calls, b.calls);
    }

    @Test
    public void looseNeverAsksTheModel() {
        Finder b = new Finder();
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 3);
        assertTrue(d.detect(in(BODY, 10_000)).person);
        assertEquals(0, b.calls);
    }

    @Test
    public void modelIsRateLimitedAndSlowerWhenWarm() {
        Finder b = new Finder();
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 1);
        assertTrue(d.detect(in(BODY, 10_000)).person);
        assertSame(PersonDetector.Result.SKIPPED, d.detect(in(BODY, 10_400)));
        assertEquals(1, b.calls);
        assertTrue(d.detect(in(BODY, 11_000)).person);
        PersonDetector.Input warm = in(BODY, 12_000);
        warm.thermal = ThermalPolicy.Level.WARM;
        assertSame(PersonDetector.Result.SKIPPED, d.detect(warm));
        warm = in(BODY, 13_000);
        warm.thermal = ThermalPolicy.Level.WARM;
        assertTrue(d.detect(warm).person);
        assertTrue(HybridPersonDetector.modelIntervalMs(ThermalPolicy.Level.NORMAL) < HybridPersonDetector.modelIntervalMs(ThermalPolicy.Level.WARM));
    }

    @Test
    public void criticalHeatSkipsTheModel() {
        Finder b = new Finder();
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 1);
        PersonDetector.Input hot = in(BODY, 10_000);
        hot.thermal = ThermalPolicy.Level.CRITICAL;
        assertFalse(d.detect(hot).person);
        assertEquals(0, b.calls);
    }

    @Test
    public void lightBuildReportsNoModelAndStillWorks() {
        Faces f = new Faces();
        f.conf = 0.6f;
        HybridPersonDetector d = new HybridPersonDetector(f, 1);
        assertEquals("none", d.bodyState());
        assertTrue(d.detect(in(BODY, 10_000)).person);
        f.conf = 0f;
        assertFalse(d.detect(in(BODY, 20_000)).person);
        assertEquals("ok", new HybridPersonDetector(f, new Finder(), 1).bodyState());
    }

    @Test
    public void whileRecordingWithoutMotionTheWholePictureIsChecked() {
        Finder b = new Finder();
        HybridPersonDetector d = new HybridPersonDetector(new Faces(), b, 1);
        PersonDetector.Input in = in(new boolean[GW * GH], 10_000);
        in.recording = true;
        assertTrue(d.detect(in).person);
        assertEquals(1, b.calls);
        in = in(new boolean[GW * GH], 12_000);
        in.recording = false;
        assertFalse("nothing moves and nothing records: no check", d.detect(in).person);
        assertEquals(1, b.calls);
    }

    private static void assertSame(Object a, Object b) {
        org.junit.Assert.assertSame(a, b);
    }
}
