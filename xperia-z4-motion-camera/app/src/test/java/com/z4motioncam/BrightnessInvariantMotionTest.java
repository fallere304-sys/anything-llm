package com.z4motioncam;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Random;
import org.junit.Test;

/**
 * A change of brightness alone must never be motion; a moving object must be. The scene is a
 * synthetic, textured room (furniture-like blocks plus a fine pattern); each test re-lights it in
 * one of the ways that happen in a real room and feeds the frames to {@link MotionDetector}.
 */
public class BrightnessInvariantMotionTest {
    private static final int W = 640;
    private static final int H = 480;
    private static final float AREA = 0.015f;

    /** How the room is lit in one frame. */
    private static final class Light {
        float gain = 1f;
        float offset = 0f;
        float gamma = 1f;
        /** Brightness added per pixel along x (a ramp across the picture). */
        float ramp = 0f;
        float noise = 1.5f;
        /** Local pool of light (a sunny patch): centre, radius, extra gain and offset at its centre. */
        float spotX = -1000f;
        float spotY = 0f;
        float spotR = 80f;
        float spotGain = 0f;
        float spotOffset = 0f;
        /** Lamp-like light that falls off with the distance from (lampX, lampY); 0 = off. */
        float lamp = 0f;
        float lampX = 100f;
        float lampY = 100f;
    }

    private static float[] base;

    private static float[] base() {
        if (base != null) return base;
        float[] b = new float[W * H];
        Random r = new Random(7);
        float[] fine = new float[W * H];
        for (int i = 0; i < fine.length; i++) fine[i] = (r.nextFloat() - 0.5f) * 14f;
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                float v = 95f + 35f * (float) (Math.sin(x / 37.0) * Math.cos(y / 29.0)) + 20f * (float) Math.sin((x + y) / 11.0);
                b[y * W + x] = v + fine[y * W + x];
            }
        }
        block(b, 40, 300, 150, 140, 40f);
        block(b, 380, 280, 120, 160, 190f);
        block(b, 250, 60, 100, 70, 200f);
        block(b, 520, 40, 80, 120, 25f);
        base = b;
        return b;
    }

    private static void block(float[] b, int x0, int y0, int w, int h, float v) {
        for (int y = y0; y < y0 + h; y++) for (int x = x0; x < x0 + w; x++) b[y * W + x] = v + (b[y * W + x] - 95f) * 0.3f;
    }

    /** Renders the room under the given light, optionally with a dark person-like ellipse at (px, py). */
    private static byte[] render(Light l, int px, int py, long seed) {
        float[] b = base();
        byte[] f = new byte[W * H * 3 / 2];
        Random r = new Random(seed);
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                float v = b[y * W + x];
                if (px >= 0) {
                    float dx = (x - px) / 45f;
                    float dy = (y - py) / 100f;
                    if (dx * dx + dy * dy < 1f) v = 20f + (b[y * W + x] - 95f) * 0.1f;
                }
                float lit = v;
                if (l.lamp > 0f) {
                    float d = (float) Math.hypot(x - l.lampX, y - l.lampY);
                    lit = v * (1f + l.lamp * 600f / (600f + d));
                }
                lit = lit * l.gain + l.offset + l.ramp * x;
                if (l.spotGain != 0f || l.spotOffset != 0f) {
                    float d2 = ((x - l.spotX) * (x - l.spotX) + (y - l.spotY) * (y - l.spotY)) / (l.spotR * l.spotR);
                    float w = (float) Math.exp(-d2);
                    lit = lit * (1f + l.spotGain * w) + l.spotOffset * w;
                }
                if (l.gamma != 1f) lit = 255f * (float) Math.pow(Math.max(0f, Math.min(255f, lit)) / 255f, l.gamma);
                lit += (float) r.nextGaussian() * l.noise;
                f[y * W + x] = (byte) Math.max(0, Math.min(255, Math.round(lit)));
            }
        }
        for (int i = W * H; i < f.length; i++) f[i] = (byte) 128;
        return f;
    }

    private static MotionDetector settled(Light l) {
        MotionDetector d = MotionDetector.forFrame(W, H);
        for (int i = 0; i < 4; i++) d.update(render(l, -1, 0, 100 + i), W, H, AREA);
        return d;
    }

    private static void assertQuiet(MotionDetector d, Light l, int frames, String what) {
        for (int i = 0; i < frames; i++) {
            boolean m = d.update(render(l, -1, 0, 500 + i), W, H, AREA);
            assertFalse(what + " (frame " + i + ", ratio " + d.lastRatio() + ")", m);
            assertTrue(what + " ratio " + d.lastRatio(), d.lastRatio() < AREA);
        }
    }

    @Test
    public void staticRoomWithNoiseIsQuiet() {
        Light l = new Light();
        MotionDetector d = settled(l);
        assertQuiet(d, l, 30, "static");
    }

    @Test
    public void lightsOnAndOffAreNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        Light on = new Light();
        on.gain = 1.7f;
        assertQuiet(d, on, 8, "lights on");
        Light off = new Light();
        off.gain = 0.45f;
        assertQuiet(d, off, 8, "lights off");
        assertQuiet(d, l, 8, "back to normal");
    }

    @Test
    public void globalOffsetAndGammaAreNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        Light bright = new Light();
        bright.offset = 45f;
        assertQuiet(d, bright, 8, "offset +45");
        Light dim = new Light();
        dim.offset = -40f;
        assertQuiet(d, dim, 8, "offset -40");
        Light g1 = new Light();
        g1.gamma = 0.6f;
        assertQuiet(d, g1, 8, "gamma 0.6");
        Light g2 = new Light();
        g2.gamma = 1.6f;
        assertQuiet(d, g2, 8, "gamma 1.6");
    }

    @Test
    public void brightnessRampAcrossThePictureIsNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        Light ramp = new Light();
        ramp.ramp = 0.09f; // +0 at the left edge, +58 at the right edge
        assertQuiet(d, ramp, 8, "horizontal ramp");
        Light ramp2 = new Light();
        ramp2.ramp = -0.07f;
        ramp2.gain = 1.2f;
        assertQuiet(d, ramp2, 8, "reverse ramp and gain");
    }

    @Test
    public void slowAutoExposureDriftIsNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        for (int i = 1; i <= 24; i++) {
            Light s = new Light();
            s.gain = 1f + 0.03f * i; // +3% per analysis up to 1.72
            s.noise = 1.5f + 0.05f * i;
            boolean m = d.update(render(s, -1, 0, 900 + i), W, H, AREA);
            assertFalse("exposure drift " + i + " ratio " + d.lastRatio(), m);
        }
    }

    @Test
    public void standbyToRecordingExposureSwitchIsNotMotion() {
        // 5 fps standby -> 10+ fps recording: the exposure steps and the noise level changes.
        Light a = new Light();
        a.noise = 3f;
        MotionDetector d = settled(a);
        Light b = new Light();
        b.gain = 0.8f;
        b.noise = 1.2f;
        assertQuiet(d, b, 6, "exposure switch");
        Light c = new Light();
        c.gain = 1.25f;
        c.gamma = 0.9f;
        c.noise = 2.2f;
        assertQuiet(d, c, 6, "exposure switch back");
    }

    @Test
    public void sunnyPatchAppearingAndDriftingIsNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        for (int i = 0; i < 20; i++) {
            Light s = new Light();
            s.spotX = 200f + 6f * i;
            s.spotY = 260f;
            s.spotR = 90f;
            s.spotGain = 0.8f;
            s.spotOffset = 30f;
            boolean m = d.update(render(s, -1, 0, 1000 + i), W, H, AREA);
            assertFalse("sunny patch " + i + " ratio " + d.lastRatio(), m);
        }
    }

    @Test
    public void spotLightSwitchedOnAndOffIsNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        Light on = new Light();
        on.spotX = 400f;
        on.spotY = 200f;
        on.spotR = 120f;
        on.spotGain = 1.2f;
        on.spotOffset = 20f;
        assertQuiet(d, on, 8, "spot on");
        assertQuiet(d, l, 8, "spot off");
    }

    @Test
    public void lampLightingUpTheRoomUnevenlyIsNotMotion() {
        Light dark = new Light();
        dark.gain = 0.25f;
        dark.noise = 2.5f;
        MotionDetector d = settled(dark);
        Light lamp = new Light();
        lamp.gain = 0.25f;
        lamp.lamp = 3f;
        lamp.noise = 1.5f;
        assertQuiet(d, lamp, 8, "lamp on in a dark room");
        assertQuiet(d, dark, 8, "lamp off");
    }

    @Test
    public void saturatingLightIsNotMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        Light blown = new Light();
        blown.gain = 2.6f; // the bright furniture clips to white
        assertQuiet(d, blown, 8, "clipped");
    }

    @Test
    public void darkNoisyRoomIsQuiet() {
        Light dark = new Light();
        dark.gain = 0.12f;
        dark.noise = 4f;
        MotionDetector d = settled(dark);
        assertQuiet(d, dark, 30, "dark sensor noise");
        Light darker = new Light();
        darker.gain = 0.1f;
        darker.noise = 5f;
        assertQuiet(d, darker, 8, "darker");
    }

    @Test
    public void personWalkingInIsMotion() {
        Light l = new Light();
        MotionDetector d = settled(l);
        boolean motion = false;
        for (int i = 0; i < 6 && !motion; i++) motion = d.update(render(l, 120 + 25 * i, 260, 2000 + i), W, H, AREA);
        assertTrue("a person walking in", motion);
        assertTrue(d.lastRatio() > 0.03f);
    }

    @Test
    public void personWalkingWhileTheLightChangesIsStillFoundAfterItSettles() {
        Light l = new Light();
        MotionDetector d = settled(l);
        Light on = new Light();
        on.gain = 1.6f;
        boolean motion = false;
        for (int i = 0; i < 10 && !motion; i++) motion = d.update(render(on, 120 + 25 * i, 260, 3000 + i), W, H, AREA);
        assertTrue("person found within a few analyses of a lighting step", motion);
    }

    @Test
    public void personInTheDarkIsMotion() {
        Light dark = new Light();
        dark.gain = 0.35f;
        dark.noise = 2.5f;
        MotionDetector d = settled(dark);
        boolean motion = false;
        for (int i = 0; i < 6 && !motion; i++) motion = d.update(render(dark, 150 + 25 * i, 260, 4000 + i), W, H, AREA);
        assertTrue("moving shape in a dim room", motion);
    }

    @Test
    public void personMovingUnderASunnyPatchIsMotion() {
        Light s = new Light();
        s.spotX = 300f;
        s.spotY = 260f;
        s.spotR = 120f;
        s.spotGain = 0.8f;
        s.spotOffset = 30f;
        MotionDetector d = settled(s);
        boolean motion = false;
        for (int i = 0; i < 6 && !motion; i++) motion = d.update(render(s, 200 + 30 * i, 260, 5000 + i), W, H, AREA);
        assertTrue(motion);
    }

    @Test
    public void handWaveOnTheSameSpotKeepsItActive() {
        Light l = new Light();
        MotionDetector d = settled(l);
        int hits = 0;
        for (int i = 0; i < 12; i++) {
            if (d.update(render(l, 300 + (i % 2) * 40, 250, 6000 + i), W, H, AREA)) hits++;
        }
        assertTrue("a back-and-forth movement keeps triggering: " + hits, hits >= 6);
    }
}
