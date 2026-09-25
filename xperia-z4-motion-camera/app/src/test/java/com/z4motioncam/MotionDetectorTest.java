package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Random;
import org.junit.Test;

public class MotionDetectorTest {
    private static final int W = 640;
    private static final int H = 480;

    private static byte[] frame(int luma) {
        byte[] f = new byte[W * H * 3 / 2];
        Arrays.fill(f, 0, W * H, (byte) luma);
        Arrays.fill(f, W * H, f.length, (byte) 128);
        return f;
    }

    private static void rect(byte[] f, int x0, int y0, int w, int h, int luma) {
        for (int y = y0; y < y0 + h; y++) Arrays.fill(f, y * W + x0, y * W + x0 + w, (byte) luma);
    }

    @Test
    public void staticSceneIsNotMotion() {
        MotionDetector d = MotionDetector.forFrame(W, H);
        for (int i = 0; i < 10; i++) assertFalse(d.update(frame(80), W, H, 0.015f));
        assertEquals(0f, d.lastRatio(), 0f);
    }

    @Test
    public void sensorNoiseIsNotMotion() {
        MotionDetector d = MotionDetector.forFrame(W, H);
        Random r = new Random(1);
        for (int i = 0; i < 20; i++) {
            byte[] f = frame(60);
            for (int p = 0; p < W * H; p++) f[p] = (byte) (60 + r.nextInt(31) - 15); // +-15 per pixel
            assertFalse(d.update(f, W, H, 0.005f));
        }
    }

    @Test
    public void globalBrightnessChangeIsNotMotion() {
        MotionDetector d = MotionDetector.forFrame(W, H);
        d.update(frame(80), W, H, 0.015f);
        // Auto exposure jumps the whole image by +40.
        assertFalse(d.update(frame(120), W, H, 0.015f));
        assertFalse(d.update(frame(120), W, H, 0.015f));
    }

    @Test
    public void movingObjectTriggersAfterTwoHits() {
        MotionDetector d = MotionDetector.forFrame(W, H);
        d.update(frame(80), W, H, 0.015f);
        byte[] a = frame(80);
        rect(a, 100, 100, 120, 120, 200);
        assertFalse("first hit is debounced", d.update(a, W, H, 0.015f));
        assertTrue(d.lastRatio() > 0.015f);
        byte[] b = frame(80);
        rect(b, 160, 120, 120, 120, 200);
        assertTrue(d.update(b, W, H, 0.015f));
    }

    @Test
    public void smallMovementRespectsSensitivity() {
        byte[] base = frame(80);
        byte[] moved = frame(80);
        rect(moved, 300, 200, 50, 50, 200); // ~ 0.8% of the frame
        MotionDetector high = MotionDetector.forFrame(W, H);
        high.update(base, W, H, 0.005f);
        high.update(moved, W, H, 0.005f);
        assertTrue(high.update(moved, W, H, 0.005f));

        MotionDetector low = MotionDetector.forFrame(W, H);
        low.update(base, W, H, 0.04f);
        low.update(moved, W, H, 0.04f);
        assertFalse(low.update(moved, W, H, 0.04f));
    }

    @Test
    public void backgroundAdaptsToNewStaticScene() {
        MotionDetector d = MotionDetector.forFrame(W, H);
        d.update(frame(80), W, H, 0.015f);
        byte[] changed = frame(80);
        rect(changed, 0, 0, 320, 240, 200); // e.g. a blanket moved and stays there
        boolean still = true;
        for (int i = 0; i < 40; i++) still = d.update(changed, W, H, 0.015f);
        assertFalse("settles back to no-motion", still);
    }

    @Test
    public void supportsWideFrames() {
        MotionDetector d = MotionDetector.forFrame(1280, 720);
        byte[] f = new byte[1280 * 720 * 3 / 2];
        d.update(f, 1280, 720, 0.015f);
        assertFalse(d.update(f, 1280, 720, 0.015f));
    }
}
