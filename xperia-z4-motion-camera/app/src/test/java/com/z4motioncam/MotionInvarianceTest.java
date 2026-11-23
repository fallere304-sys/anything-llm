package com.z4motioncam;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import org.junit.Test;

/** Brightness changes alone are not motion; a moving object is. Synthetic frames only. */
public class MotionInvarianceTest {
    private static final float AREA = 0.005f; // the most sensitive setting
    private static final List<SynthScene.Obj> NONE = new ArrayList<>();

    private interface LightAt {
        SynthScene.Light at(int i);
    }

    /** Warms up on the plain scene, then feeds 'n' frames lit by 'light'; returns max ratio, asserting no trigger. */
    private static float runStatic(SynthScene s, LightAt light, int n, String name) {
        MotionDetector d = MotionDetector.forFrame(s.w, s.h);
        for (int i = 0; i < 3; i++) d.update(s.frame(new SynthScene.Light().noise(0.0001f, i), NONE), s.w, s.h, AREA);
        float max = 0;
        for (int i = 0; i < n; i++) {
            boolean m = d.update(s.frame(light.at(i), NONE), s.w, s.h, AREA);
            max = Math.max(max, d.lastRatio());
            assertFalse(name + " frame " + i + " ratio " + d.lastRatio(), m);
        }
        return max;
    }

    private static void allResolutions(String name, LightAt l) {
        int[][] sizes = {{320, 240}, {640, 480}, {1280, 720}, {1920, 1080}};
        for (int[] sz : sizes) runStatic(new SynthScene(sz[0], sz[1], 7), l, 10, name + " " + sz[0]);
    }

    @Test public void wholeFrameGainJumpUp() { allResolutions("gain1.6", i -> new SynthScene.Light().gain(1.6f)); }
    @Test public void wholeFrameGainJumpDown() { allResolutions("gain0.4", i -> new SynthScene.Light().gain(0.4f)); }
    @Test public void gammaChange() {
        allResolutions("gamma0.6", i -> new SynthScene.Light().gamma(0.6f));
        allResolutions("gamma1.7", i -> new SynthScene.Light().gamma(1.7f));
    }
    @Test public void brightnessRamp() { allResolutions("ramp", i -> new SynthScene.Light().ramp(0.6f, 1.3f)); }
    @Test public void gradualFade() { allResolutions("fade", i -> new SynthScene.Light().gain(1f - 0.05f * i)); }
    @Test public void flickeringExposure() {
        Random r = new Random(3);
        float[] g = new float[10];
        for (int i = 0; i < g.length; i++) g[i] = 0.6f + r.nextFloat() * 0.8f;
        allResolutions("flicker", i -> new SynthScene.Light().gain(g[i]));
    }
    @Test public void sunPatchOnTheFloor() {
        // Soft-edged patch of sunlight growing and drifting across part of the picture.
        allResolutions("sun", i -> new SynthScene.Light().spot(200 + 8 * i, 220, 70, 1.0f + 0.1f * i));
        allResolutions("sun-strong", i -> new SynthScene.Light().spot(300, 250, 90, 1.8f));
    }
    @Test public void sensorNoise() {
        allResolutions("noise4", i -> new SynthScene.Light().noise(4f, 100 + i));
        allResolutions("noise8", i -> new SynthScene.Light().noise(8f, 200 + i));
    }
    @Test public void noiseTogetherWithBrightnessChange() {
        allResolutions("gain+noise", i -> new SynthScene.Light().gain(1.4f).gamma(0.8f).noise(5f, 300 + i));
    }
    @Test public void saturationFromAnExposureJump() {
        allResolutions("clip", i -> new SynthScene.Light().gain(2.6f));
        allResolutions("crush", i -> new SynthScene.Light().gain(0.08f).noise(0.5f, 9 + i));
    }

    @Test public void lightsSwitchedOnInADarkRoom() {
        SynthScene s = new SynthScene(640, 480, 11);
        MotionDetector d = MotionDetector.forFrame(640, 480);
        for (int i = 0; i < 6; i++) d.update(s.frame(new SynthScene.Light().gain(0.05f).noise(1.5f, i), NONE), 640, 480, AREA);
        for (int i = 0; i < 8; i++) {
            float g = i < 2 ? 0.05f + 0.4f * i : 1f; // a short ramp, then steady
            boolean m = d.update(s.frame(new SynthScene.Light().gain(g).noise(1.5f, 50 + i), NONE), 640, 480, AREA);
            assertFalse("lights on, frame " + i, m);
        }
    }

    @Test public void lightsSwitchedOff() {
        SynthScene s = new SynthScene(640, 480, 12);
        MotionDetector d = MotionDetector.forFrame(640, 480);
        for (int i = 0; i < 6; i++) d.update(s.frame(new SynthScene.Light().noise(1.5f, i), NONE), 640, 480, AREA);
        for (int i = 0; i < 8; i++) {
            boolean m = d.update(s.frame(new SynthScene.Light().gain(0.03f).noise(1.5f, 50 + i), NONE), 640, 480, AREA);
            assertFalse("lights off, frame " + i, m);
        }
    }

    // ---- real motion still triggers ----

    private static boolean walks(SynthScene s, SynthScene.Light light, int height, int luma, boolean textured) {
        MotionDetector d = MotionDetector.forFrame(s.w, s.h);
        for (int i = 0; i < 3; i++) d.update(s.frame(light, NONE), s.w, s.h, 0.015f);
        boolean any = false;
        int step = Math.max(4, s.w / 40);
        for (int i = 0; i < 8; i++) {
            List<SynthScene.Obj> p = SynthScene.person(s.w / 4 + i * step, s.h * 9 / 10, height, luma, textured);
            any |= d.update(s.frame(light, p), s.w, s.h, 0.015f);
        }
        return any;
    }

    @Test public void aWalkingPersonIsMotionAtAllResolutions() {
        int[][] sizes = {{320, 240}, {640, 480}, {1280, 720}, {1920, 1080}};
        for (int[] sz : sizes) {
            SynthScene s = new SynthScene(sz[0], sz[1], 21);
            SynthScene.Light l = new SynthScene.Light();
            assertTrue("textured coat " + sz[0], walks(s, l, sz[1] * 6 / 10, 90, true));
            assertTrue("flat dark coat " + sz[0], walks(s, l, sz[1] * 6 / 10, 15, false));
            assertTrue("flat bright coat " + sz[0], walks(s, l, sz[1] * 6 / 10, 235, false));
        }
    }

    @Test public void aPersonOnAPlainWallIsMotion() {
        SynthScene s = SynthScene.flat(640, 480, 150);
        assertTrue(walks(s, new SynthScene.Light(), 300, 30, false));
    }

    @Test public void aPersonIsStillMotionWhileTheLightChanges() {
        SynthScene s = new SynthScene(640, 480, 22);
        assertTrue(walks(s, new SynthScene.Light().gain(0.5f).gamma(1.3f).noise(3f, 5), 300, 60, true));
    }

    @Test public void aPersonInADimRoomIsMotion() {
        SynthScene s = new SynthScene(640, 480, 23);
        assertTrue(walks(s, new SynthScene.Light().gain(0.18f).noise(1.5f, 6), 300, 235, true));
    }

    @Test public void aPersonUnderASunPatchIsMotion() {
        SynthScene s = new SynthScene(640, 480, 24);
        assertTrue(walks(s, new SynthScene.Light().spot(200, 300, 80, 1.5f), 300, 40, false));
    }

    @Test public void aStillObjectEventuallyBecomesBackground() {
        SynthScene s = new SynthScene(640, 480, 25);
        MotionDetector d = MotionDetector.forFrame(640, 480);
        SynthScene.Light l = new SynthScene.Light();
        for (int i = 0; i < 3; i++) d.update(s.frame(l, NONE), 640, 480, 0.01f);
        List<SynthScene.Obj> p = SynthScene.person(300, 430, 300, 20, false);
        boolean still = true;
        for (int i = 0; i < 40; i++) still = d.update(s.frame(l, p), 640, 480, 0.01f);
        assertFalse(still);
    }
}
