package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Random;
import org.junit.Test;

/** BlobAnalyzer, GrayFrame, HybridPersonDetector and the motion mask they build on. */
public class PersonShapeTest {
    private static final int GW = 32;
    private static final int GH = 24;

    private static boolean[] mask(int x0, int y0, int w, int h) {
        boolean[] m = new boolean[GW * GH];
        for (int y = y0; y < y0 + h; y++) for (int x = x0; x < x0 + w; x++) m[y * GW + x] = true;
        return m;
    }

    // ---- BlobAnalyzer ----

    @Test
    public void emptyMaskHasNoBlob() {
        BlobAnalyzer.Blob b = BlobAnalyzer.largest(new boolean[GW * GH], GW, GH);
        assertEquals(0, b.cells);
        assertFalse(b.personLike());
    }

    @Test
    public void malformedInputIsHarmless() {
        assertEquals(0, BlobAnalyzer.largest(null, GW, GH).cells);
        assertEquals(0, BlobAnalyzer.largest(new boolean[3], GW, GH).cells);
        assertEquals(0, BlobAnalyzer.largest(new boolean[10], 0, 0).cells);
    }

    @Test
    public void aCompactBlobIsPersonLike() {
        BlobAnalyzer.Blob b = BlobAnalyzer.largest(mask(10, 6, 6, 10), GW, GH); // 60 cells = 7.8%
        assertEquals(60, b.cells);
        assertEquals(6, b.width);
        assertEquals(10, b.height);
        assertEquals(1.0f, b.fill(), 0.001f);
        assertTrue(b.personLike());
    }

    @Test
    public void aTinyBlobIsNot() {
        assertFalse(BlobAnalyzer.largest(mask(5, 5, 3, 3), GW, GH).personLike()); // 9 cells = 1.2%
    }

    @Test
    public void aWholeFrameChangeIsNot() {
        assertFalse(BlobAnalyzer.largest(mask(0, 0, GW, GH), GW, GH).personLike());
        assertFalse(BlobAnalyzer.largest(mask(0, 0, GW, 20), GW, GH).personLike()); // 83%
    }

    @Test
    public void scatteredNoiseIsNot() {
        boolean[] m = new boolean[GW * GH];
        Random r = new Random(5);
        for (int i = 0; i < 90; i++) m[r.nextInt(m.length)] = true; // about 12% of cells, spread out
        assertFalse(BlobAnalyzer.largest(m, GW, GH).personLike());
    }

    @Test
    public void aThinStripeIsNot() {
        assertFalse(BlobAnalyzer.largest(mask(2, 10, 30, 1), GW, GH).personLike()); // a line: aspect and area
        assertFalse(BlobAnalyzer.largest(mask(10, 2, 2, 20), GW, GH).personLike()); // 40 cells, aspect 0.1
    }

    @Test
    public void picksTheLargestOfSeveralGroups() {
        boolean[] m = mask(1, 1, 3, 3);
        boolean[] big = mask(15, 8, 7, 9);
        for (int i = 0; i < m.length; i++) m[i] |= big[i];
        BlobAnalyzer.Blob b = BlobAnalyzer.largest(m, GW, GH);
        assertEquals(63, b.cells);
        assertTrue(b.personLike());
    }

    @Test
    public void diagonalCellsAreNotConnected() {
        boolean[] m = new boolean[GW * GH];
        for (int i = 0; i < 8; i++) m[(5 + i) * GW + 5 + i] = true;
        assertEquals(1, BlobAnalyzer.largest(m, GW, GH).cells);
    }

    @Test
    public void anLShapeIsOneGroup() {
        boolean[] m = mask(4, 4, 8, 2);
        boolean[] leg = mask(4, 4, 2, 8);
        for (int i = 0; i < m.length; i++) m[i] |= leg[i];
        assertEquals(16 + 16 - 4, BlobAnalyzer.largest(m, GW, GH).cells);
    }

    // ---- GrayFrame ----

    @Test
    public void outputSizeIsEvenAndBounded() {
        assertArrayEquals2(new int[] {320, 240}, GrayFrame.outputSize(640, 480, 0));
        assertArrayEquals2(new int[] {240, 320}, GrayFrame.outputSize(640, 480, 90));
        assertArrayEquals2(new int[] {320, 240}, GrayFrame.outputSize(640, 480, 180));
        assertArrayEquals2(new int[] {320, 180}, GrayFrame.outputSize(1920, 1080, 0));
        assertArrayEquals2(new int[] {320, 240}, GrayFrame.outputSize(320, 240, 0));
        int[] small = GrayFrame.outputSize(100, 50, 0);
        assertEquals(0, small[0] % 2);
        assertEquals(0, small[1] % 2);
        assertTrue(small[0] <= 100);
    }

    private static void assertArrayEquals2(int[] expected, int[] actual) {
        assertEquals(expected[0], actual[0]);
        assertEquals(expected[1], actual[1]);
    }

    private static short gray(int y) {
        return (short) (((y >> 3) << 11) | ((y >> 2) << 5) | (y >> 3));
    }

    /** A 640x480 frame with a bright 64x48 patch in one corner. */
    private static byte[] cornerFrame(int cx, int cy) {
        byte[] f = new byte[640 * 480 * 3 / 2];
        Arrays.fill(f, 0, 640 * 480, (byte) 40);
        for (int y = cy; y < cy + 48; y++) Arrays.fill(f, y * 640 + cx, y * 640 + cx + 64, (byte) 240);
        return f;
    }

    private static short[] convert(byte[] f, int rotation) {
        int[] o = GrayFrame.outputSize(640, 480, rotation);
        short[] out = new short[o[0] * o[1]];
        GrayFrame.toRgb565(f, 640, 480, rotation, out);
        return out;
    }

    private static boolean bright(short[] px, int w, int x, int y) {
        return px[y * w + x] == gray(240);
    }

    @Test
    public void greyConversionKeepsLevels() {
        short[] px = convert(cornerFrame(0, 0), 0);
        assertEquals(gray(40), px[319 * 1 + 100 * 320]);
        assertEquals(gray(240), px[2]);
    }

    @Test
    public void rotation0KeepsTheCorner() {
        short[] px = convert(cornerFrame(0, 0), 0); // patch top-left
        assertTrue(bright(px, 320, 2, 2));
        assertFalse(bright(px, 320, 317, 237));
    }

    @Test
    public void rotation90MovesTopLeftToTopRight() {
        // Rotating a picture clockwise by 90 degrees puts its top-left corner at the top-right.
        short[] px = convert(cornerFrame(0, 0), 90);
        assertTrue(bright(px, 240, 237, 2));
        assertFalse(bright(px, 240, 2, 2));
    }

    @Test
    public void rotation180MovesTopLeftToBottomRight() {
        short[] px = convert(cornerFrame(0, 0), 180);
        assertTrue(bright(px, 320, 317, 237));
        assertFalse(bright(px, 320, 2, 2));
    }

    @Test
    public void rotation270MovesTopLeftToBottomLeft() {
        short[] px = convert(cornerFrame(0, 0), 270);
        assertTrue(bright(px, 240, 2, 317));
        assertFalse(bright(px, 240, 237, 2));
    }

    @Test
    public void negativeAndLargeRotationsAreNormalized() {
        short[] a = convert(cornerFrame(0, 0), 90);
        short[] b = convert(cornerFrame(0, 0), 450);
        short[] c = convert(cornerFrame(0, 0), -270);
        assertTrue(Arrays.equals(a, b));
        assertTrue(Arrays.equals(a, c));
    }

    @Test
    public void smallFramesAreNotUpscaled() {
        byte[] f = new byte[160 * 120 * 3 / 2];
        Arrays.fill(f, (byte) 100);
        int[] o = GrayFrame.outputSize(160, 120, 0);
        assertEquals(160, o[0]);
        short[] out = new short[o[0] * o[1]];
        GrayFrame.toRgb565(f, 160, 120, 0, out);
        assertEquals(gray(100), out[0]);
        assertEquals(gray(100), out[out.length - 1]);
    }

    // ---- HybridPersonDetector ----

    private static final class FakeFaces implements HybridPersonDetector.FaceFinder {
        float confidence;
        int calls;
        int w;
        int h;
        boolean closed;

        @Override
        public float bestConfidence(short[] rgb565, int width, int height) {
            calls++;
            w = width;
            h = height;
            return confidence;
        }

        @Override
        public void close() {
            closed = true;
        }
    }

    private static PersonDetector.Input input(boolean[] mask) {
        PersonDetector.Input in = new PersonDetector.Input();
        in.nv21 = new byte[640 * 480 * 3 / 2];
        in.width = 640;
        in.height = 480;
        in.rotation = 90;
        in.motionMask = mask;
        in.gridW = GW;
        in.gridH = GH;
        return in;
    }

    @Test
    public void aClearFaceIsAPerson() {
        FakeFaces f = new FakeFaces();
        f.confidence = 0.55f;
        PersonDetector.Result r = new HybridPersonDetector(f, 2).detect(input(null));
        assertTrue(r.person);
        assertEquals("顔", r.basis);
        assertEquals(240, f.w); // upright (rotated) picture
        assertEquals(320, f.h);
    }

    @Test
    public void thresholdsFollowTheSensitivity() {
        FakeFaces f = new FakeFaces();
        f.confidence = 0.45f;
        assertFalse(new HybridPersonDetector(f, 1).detect(input(null)).person);
        assertTrue(new HybridPersonDetector(f, 2).detect(input(null)).person);
        f.confidence = 0.35f;
        assertFalse(new HybridPersonDetector(f, 2).detect(input(null)).person);
        assertTrue(new HybridPersonDetector(f, 3).detect(input(null)).person);
        f.confidence = 0.2f;
        assertFalse(new HybridPersonDetector(f, 3).detect(input(null)).person);
    }

    @Test
    public void shapeEvidenceOnlyAtTheLooseSetting() {
        FakeFaces f = new FakeFaces(); // no face at all
        boolean[] m = mask(10, 6, 6, 10);
        assertFalse(new HybridPersonDetector(f, 1).detect(input(m)).person);
        assertFalse(new HybridPersonDetector(f, 2).detect(input(m)).person);
        PersonDetector.Result r = new HybridPersonDetector(f, 3).detect(input(m));
        assertTrue(r.person);
        assertEquals("動く塊", r.basis);
    }

    @Test
    public void looseSettingStillRejectsALightSwitch() {
        FakeFaces f = new FakeFaces();
        assertFalse(new HybridPersonDetector(f, 3).detect(input(mask(0, 0, GW, GH))).person);
    }

    @Test
    public void sensitivityIsClamped() {
        FakeFaces f = new FakeFaces();
        f.confidence = 0.35f;
        assertTrue(new HybridPersonDetector(f, 99).detect(input(null)).person);
        assertFalse(new HybridPersonDetector(f, -4).detect(input(null)).person);
    }

    @Test
    public void closeReachesTheFinder() {
        FakeFaces f = new FakeFaces();
        new HybridPersonDetector(f, 2).close();
        assertTrue(f.closed);
    }

    @Test
    public void thresholdsAreOrdered() {
        assertTrue(HybridPersonDetector.faceThreshold(1) > HybridPersonDetector.faceThreshold(2));
        assertTrue(HybridPersonDetector.faceThreshold(2) > HybridPersonDetector.faceThreshold(3));
    }

    // ---- the mask exposed by MotionDetector ----

    @Test
    public void motionDetectorExposesTheChangedCells() {
        MotionDetector d = MotionDetector.forFrame(640, 480);
        byte[] bg = new byte[640 * 480 * 3 / 2];
        Arrays.fill(bg, 0, 640 * 480, (byte) 60);
        d.update(bg, 640, 480, 0.015f);
        byte[] moved = bg.clone();
        for (int y = 100; y < 300; y++) Arrays.fill(moved, y * 640 + 200, y * 640 + 360, (byte) 200);
        d.update(moved, 640, 480, 0.015f);
        boolean[] m = d.changedMask();
        assertEquals(d.gridW() * d.gridH(), m.length);
        BlobAnalyzer.Blob b = BlobAnalyzer.largest(m, d.gridW(), d.gridH());
        assertTrue("changed cells found: " + b.cells, b.cells >= 10);
        assertTrue(b.personLike());
        // The whole picture shifting in brightness leaves (almost) no changed cells.
        MotionDetector e = MotionDetector.forFrame(640, 480);
        e.update(bg, 640, 480, 0.015f);
        byte[] bright = new byte[bg.length];
        Arrays.fill(bright, 0, 640 * 480, (byte) 110);
        e.update(bright, 640, 480, 0.015f);
        assertEquals(0, BlobAnalyzer.largest(e.changedMask(), e.gridW(), e.gridH()).cells);
    }

    @Test
    public void maskIsClearedByReset() {
        MotionDetector d = MotionDetector.forFrame(640, 480);
        byte[] a = new byte[640 * 480 * 3 / 2];
        d.update(a, 640, 480, 0.01f);
        byte[] b = a.clone();
        Arrays.fill(b, 0, 640 * 240, (byte) 255);
        d.update(b, 640, 480, 0.01f);
        d.reset();
        for (boolean c : d.changedMask()) assertFalse(c);
    }
}
