package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;
import org.junit.Test;

/**
 * Synthetic scenes through the real chain (MotionDetector, then HybridPersonDetector's shape check).
 * Sizes follow two real sample pictures (not committed): a backlit person at the edge, 28% x 66% of
 * the picture; a dark silhouette in a doorway, 9% x 39% (about 3% of the area).
 */
public class ShapeScenarioTest {
    private static final long STEP_MS = 350;

    private static final class NoFaces implements HybridPersonDetector.FaceFinder {
        public float bestConfidence(short[] rgb565, int width, int height) { return 0f; }
        public void close() {}
    }

    private interface Frames {
        List<SynthScene.Obj> at(int i, int w, int h);
    }

    /** Runs a scenario and reports, for the given level, whether a person was decided in the last 8 analyses. */
    private static boolean decide(SynthScene s, SynthScene.Light light, int n, int level, int rotation, Frames fr) {
        MotionDetector d = MotionDetector.forFrame(s.w, s.h);
        HybridPersonDetector h = new HybridPersonDetector(new NoFaces(), level);
        PersonDetector.Input in = new PersonDetector.Input();
        in.width = s.w;
        in.height = s.h;
        in.rotation = rotation;
        in.gridW = d.gridW();
        in.gridH = d.gridH();
        boolean any = false;
        // Three empty frames first (the room as it is), then the scenario.
        for (int i = 0; i < n; i++) {
            long now = 1000 + i * STEP_MS;
            byte[] f = s.frame(light, i < 3 ? new ArrayList<SynthScene.Obj>() : fr.at(i - 3, s.w, s.h));
            d.update(f, s.w, s.h, CameraPipeline.PERSON_GATE_AREA);
            h.observeMotion(d.changedMask(), d.gridW(), d.gridH(), rotation, now);
            in.nv21 = f;
            in.motionMask = d.changedMask();
            in.nowMs = now;
            PersonDetector.Result r = h.detect(in);
            if (i >= n - 8 && r.person) any = true;
        }
        return any;
    }

    private static List<SynthScene.Obj> one(int x, int y, int w, int h, int luma, boolean tex) {
        List<SynthScene.Obj> l = new ArrayList<>();
        l.add(new SynthScene.Obj(x, y, w, h, luma, tex));
        return l;
    }

    /** A person of the given size share who shuffles sideways (feet on the floor line). */
    private static Frames person(float wFrac, float hFrac, int luma, float startX, float speed) {
        return (i, w, h) -> {
            int pw = (int) (w * wFrac);
            int ph = (int) (h * hFrac);
            int x = (int) (w * (startX + speed * i));
            return one(x, (int) (h * 0.93f) - ph, pw, ph, luma, false);
        };
    }

    private static final int[] SIZES = {320, 640, 1280};

    private static SynthScene scene(int w, long seed) {
        return new SynthScene(w, w * 3 / 4, seed);
    }

    @Test public void backlitBigPersonLikeSample1AtAllLevels() {
        for (int w : SIZES) {
            for (int level = 1; level <= 3; level++) {
                assertTrue("w=" + w + " level " + level,
                        decide(scene(w, 31), new SynthScene.Light().noise(1.5f, 1), 14, level, 0, person(0.28f, 0.66f, 20, 0.45f, 0.015f)));
            }
        }
    }

    @Test public void darkDoorwaySilhouetteLikeSample2() {
        SynthScene.Light dark = new SynthScene.Light().gain(0.2f).noise(1.2f, 2);
        for (int w : SIZES) {
            Frames p = person(0.09f, 0.39f, 1, 0.50f, 0.012f);
            assertTrue("loose " + w, decide(scene(w, 32), dark, 14, 3, 0, p));
            assertTrue("standard " + w, decide(scene(w, 32), dark, 14, 2, 0, p));
        }
    }

    @Test public void aDistantSmallPersonOnlyWhenLoose() {
        Frames p = person(0.05f, 0.22f, 20, 0.3f, 0.02f);
        assertTrue(decide(scene(640, 33), new SynthScene.Light(), 14, 3, 0, p));
        assertFalse(decide(scene(640, 33), new SynthScene.Light(), 14, 1, 0, p));
    }

    @Test public void aSittingPersonIsFound() {
        Frames p = person(0.22f, 0.30f, 30, 0.4f, 0.01f);
        assertTrue(decide(scene(640, 34), new SynthScene.Light(), 14, 2, 0, p));
    }

    @Test public void aLyingPersonIsFoundWhenStandardOrLoose() {
        Frames p = (i, w, h) -> one((int) (w * (0.30f + 0.01f * i)), (int) (h * 0.70f), (int) (w * 0.38f), (int) (h * 0.16f), 25, false);
        assertTrue(decide(scene(640, 35), new SynthScene.Light(), 14, 2, 0, p));
        assertTrue(decide(scene(640, 35), new SynthScene.Light(), 14, 3, 0, p));
    }

    @Test public void aSwingingCurtainIsNotAPerson() {
        // Wide curtain hanging from the top, swaying in place: no level accepts it.
        Frames c = (i, w, h) -> {
            int sway = (int) (Math.sin(i * 1.4) * w * 0.03f);
            return one((int) (w * 0.55f) + sway, 0, (int) (w * 0.34f), (int) (h * 0.62f), 200, true);
        };
        for (int level = 1; level <= 3; level++) {
            assertFalse("level " + level, decide(scene(640, 36), new SynthScene.Light(), 24, level, 0, c));
        }
    }

    @Test public void aSmallPetIsNotAPersonUnlessLoose() {
        Frames pet = (i, w, h) -> one((int) (w * (0.2f + 0.03f * i)), (int) (h * 0.80f), (int) (w * 0.10f), (int) (h * 0.12f), 40, true);
        assertFalse(decide(scene(640, 37), new SynthScene.Light(), 14, 1, 0, pet));
        assertFalse(decide(scene(640, 37), new SynthScene.Light(), 14, 2, 0, pet));
    }

    @Test public void lightingAloneNeverMakesAPerson() {
        SynthScene s = scene(640, 38);
        for (int level = 1; level <= 3; level++) {
            final int lv = level;
            // Lights on and off, sun patch, noise: no objects at all.
            assertFalse(decide(s, new SynthScene.Light().gain(0.5f).spot(300, 250, 90, 1.8f).noise(6f, 5), 14, lv, 0,
                    (i, w, h) -> new ArrayList<>()));
        }
        MotionDetector d = MotionDetector.forFrame(640, 480);
        HybridPersonDetector h = new HybridPersonDetector(new NoFaces(), 3);
        PersonDetector.Input in = new PersonDetector.Input();
        in.width = 640; in.height = 480; in.gridW = d.gridW(); in.gridH = d.gridH();
        for (int i = 0; i < 20; i++) {
            float g = (i / 3) % 2 == 0 ? 1f : 0.25f; // switching on and off
            byte[] f = s.frame(new SynthScene.Light().gain(g).noise(2f, 40 + i), new ArrayList<>());
            d.update(f, 640, 480, CameraPipeline.PERSON_GATE_AREA);
            long now = 1000 + i * STEP_MS;
            h.observeMotion(d.changedMask(), d.gridW(), d.gridH(), 0, now);
            in.nv21 = f; in.motionMask = d.changedMask(); in.nowMs = now;
            assertFalse("frame " + i, h.detect(in).person);
        }
    }

    @Test public void aRotatedCameraStillSeesAnUprightPerson() {
        // The camera is turned 90 degrees: the person is drawn lying across the raw frame.
        Frames p = (i, w, h) -> {
            int pw = (int) (w * 0.66f);
            int ph = (int) (h * 0.28f);
            return one((int) (w * 0.10f), (int) (h * (0.45f + 0.012f * i)), pw, ph, 20, false);
        };
        assertTrue(decide(scene(640, 39), new SynthScene.Light(), 14, 1, 90, p));
        // The same drawing with no rotation is a wide lying shape: strict does not take it.
        assertFalse(decide(scene(640, 39), new SynthScene.Light(), 14, 1, 0, p));
    }

    @Test public void aMovingPersonUnderChangingLightIsStillFound() {
        SynthScene s = scene(640, 40);
        boolean[] seen = new boolean[1];
        seen[0] = decide(s, new SynthScene.Light().gain(0.6f).gamma(1.3f).noise(3f, 9), 14, 2, 0, person(0.2f, 0.6f, 40, 0.4f, 0.015f));
        assertTrue(seen[0]);
    }

    @Test public void modeSwitchKeepsBothPathsIndependent() {
        // Motion mode never consults the shape: the motion detector alone says "motion" for a person.
        SynthScene s = scene(640, 41);
        MotionDetector d = MotionDetector.forFrame(640, 480);
        boolean motion = false;
        Frames p = person(0.2f, 0.6f, 30, 0.3f, 0.02f);
        for (int i = 0; i < 8; i++) motion |= d.update(s.frame(new SynthScene.Light(), p.at(i, 640, 480)), 640, 480, 0.015f);
        assertTrue(motion);
        assertEquals(0.006f, CameraPipeline.PERSON_GATE_AREA, 0f);
    }
}
