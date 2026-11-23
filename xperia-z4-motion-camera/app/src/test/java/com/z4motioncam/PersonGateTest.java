package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PersonGateTest {
    private static final ThermalPolicy.Level N = ThermalPolicy.Level.NORMAL;

    @Test
    public void noCheckWithoutMotion() {
        PersonGate g = new PersonGate();
        assertFalse("a quiet room costs nothing", g.wantCheck(10_000, false, N));
    }

    @Test
    public void motionOpensTheGateForAFewSeconds() {
        PersonGate g = new PersonGate();
        g.onMotion(10_000);
        assertTrue(g.wantCheck(10_000, false, N));
        assertTrue(g.wantCheck(10_000 + PersonGate.MOTION_WINDOW_MS, false, N));
        assertFalse(g.wantCheck(10_000 + PersonGate.MOTION_WINDOW_MS + 1, false, N));
    }

    @Test
    public void checksAreRateLimited() {
        PersonGate g = new PersonGate();
        g.onMotion(0);
        g.onResult(1_000, false);
        g.onMotion(1_100);
        assertFalse(g.wantCheck(1_100, false, N));
        assertTrue(g.wantCheck(1_000 + PersonGate.IDLE_CHECK_MS, false, N));
    }

    @Test
    public void warmPhoneChecksLessOften() {
        assertEquals(500, PersonGate.checkIntervalMs(N, false));
        assertEquals(1000, PersonGate.checkIntervalMs(ThermalPolicy.Level.WARM, false));
        assertEquals(2000, PersonGate.checkIntervalMs(ThermalPolicy.Level.HOT, false));
        assertEquals(2000, PersonGate.checkIntervalMs(N, true));
        assertEquals(8000, PersonGate.checkIntervalMs(ThermalPolicy.Level.CRITICAL, true));
    }

    @Test
    public void oneHitIsNotEnoughToStart() {
        PersonGate g = new PersonGate();
        g.onResult(1_000, true);
        assertFalse(g.shouldStart(1_000));
    }

    @Test
    public void twoHitsInARowStart() {
        PersonGate g = new PersonGate();
        g.onResult(1_000, true);
        g.onResult(1_500, true);
        assertTrue(g.shouldStart(1_500));
    }

    @Test
    public void twoOfThreeStartsButTheLatestMustBePositive() {
        PersonGate g = new PersonGate();
        g.onResult(1_000, true);
        g.onResult(1_500, false);
        g.onResult(2_000, true);
        assertTrue(g.shouldStart(2_000));
        g.onResult(2_500, false);
        assertFalse(g.shouldStart(2_500));
    }

    @Test
    public void onlyTheLastThreeCount() {
        PersonGate g = new PersonGate();
        g.onResult(1_000, true);
        g.onResult(1_500, false);
        g.onResult(2_000, false);
        g.onResult(2_500, false);
        g.onResult(3_000, true); // history: T F F -> only one hit
        assertFalse(g.shouldStart(3_000));
    }

    @Test
    public void oldResultsAreForgotten() {
        PersonGate g = new PersonGate();
        g.onResult(1_000, true);
        g.onResult(1_000 + PersonGate.HISTORY_MAX_GAP_MS + 1, true); // a different scene
        assertFalse(g.shouldStart(1_000 + PersonGate.HISTORY_MAX_GAP_MS + 1));
    }

    @Test
    public void staleResultsDoNotStartLater() {
        PersonGate g = new PersonGate();
        g.onResult(1_000, true);
        g.onResult(1_500, true);
        assertFalse(g.shouldStart(1_500 + PersonGate.HISTORY_MAX_GAP_MS + 1));
    }

    @Test
    public void recordingChecksHappenWithoutMotion() {
        PersonGate g = new PersonGate();
        assertTrue(g.wantCheck(10_000, true, N));
        g.onResult(10_000, true);
        assertFalse(g.wantCheck(11_000, true, N));
        assertTrue(g.wantCheck(12_000, true, N));
    }

    @Test
    public void stopsAfterTheKeepTimeWithoutAPerson() {
        PersonGate g = new PersonGate();
        g.onRecordingStarted(0);
        assertFalse(g.shouldStop(10_000, 10_000));
        assertTrue(g.shouldStop(10_001, 10_000));
    }

    @Test
    public void everySightingExtendsTheRecording() {
        PersonGate g = new PersonGate();
        g.onRecordingStarted(0);
        g.onResult(8_000, true);
        assertFalse(g.shouldStop(17_000, 10_000));
        g.onResult(15_000, false);
        assertFalse(g.shouldStop(18_000, 10_000));
        assertTrue(g.shouldStop(18_001, 10_000));
    }

    @Test
    public void negativeResultsDoNotExtend() {
        PersonGate g = new PersonGate();
        g.onRecordingStarted(0);
        for (long t = 2_000; t <= 20_000; t += 2_000) g.onResult(t, false);
        assertTrue(g.shouldStop(20_000, 10_000));
    }

    @Test
    public void neverSeenMeansStop() {
        PersonGate g = new PersonGate();
        assertTrue(g.shouldStop(0, 10_000));
        assertEquals(-1, g.lastPersonMs());
    }

    @Test
    public void resetForgetsEverything() {
        PersonGate g = new PersonGate();
        g.onMotion(100);
        g.onResult(100, true);
        g.onResult(600, true);
        g.reset();
        assertFalse(g.shouldStart(600));
        assertFalse(g.wantCheck(700, false, N));
        assertEquals(-1, g.lastPersonMs());
    }

    @Test
    public void aLongRecordingKeepsGoingWhileTheFaceIsSeenHalfTheTime() {
        // FaceDetector misses often: every other check fails, the recording must not stop.
        PersonGate g = new PersonGate();
        g.onRecordingStarted(0);
        for (long t = 2_000; t <= 120_000; t += 2_000) {
            g.onResult(t, (t / 2_000) % 2 == 0);
            assertFalse("at " + t, g.shouldStop(t, 10_000));
        }
    }
}
