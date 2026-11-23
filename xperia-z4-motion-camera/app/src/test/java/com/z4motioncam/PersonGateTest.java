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

    // ---- back-off: nobody found, again and again ----

    @Test
    public void emptyChecksBackOffDoublingUpToTwelveSeconds() {
        PersonGate g = new PersonGate();
        long[] expected = {500, 500, 500, 1000, 2000, 4000, 8000, 12000, 12000, 12000};
        for (int i = 0; i < expected.length; i++) {
            assertEquals("after " + i + " empty checks", expected[i], g.intervalMs(N, false));
            g.onResult(i * 1000L, false);
        }
    }

    @Test
    public void theBackOffDoesNotApplyWhileRecording() {
        PersonGate g = new PersonGate();
        for (int i = 0; i < 10; i++) g.onResult(i * 1000L, false);
        assertEquals(12_000, g.intervalMs(N, false));
        assertEquals(PersonGate.RECORD_CHECK_MS, g.intervalMs(N, true));
    }

    @Test
    public void aPersonFoundResetsTheBackOff() {
        PersonGate g = new PersonGate();
        for (int i = 0; i < 10; i++) g.onResult(i * 1000L, false);
        g.onResult(20_000, true);
        assertEquals(PersonGate.IDLE_CHECK_MS, g.intervalMs(N, false));
        assertEquals(0, g.emptyChecks());
    }

    @Test
    public void startingARecordingAndCheckingWhileRecordingResetTheBackOff() {
        PersonGate g = new PersonGate();
        for (int i = 0; i < 10; i++) g.onResult(i * 1000L, false);
        g.onRecordingStarted(20_000);
        assertEquals(0, g.emptyChecks());
        g.onResult(22_000, false, true); // an empty check during a recording is not "idle"
        g.onResult(24_000, false, true);
        g.onResult(26_000, false, true);
        g.onResult(28_000, false, true);
        assertEquals(0, g.emptyChecks());
    }

    @Test
    public void aNewBurstOfMotionAfterAPauseGoesBackToTheFastRate() {
        PersonGate g = new PersonGate();
        g.onMotion(0);
        for (int i = 0; i < 10; i++) g.onResult(i * 500L, false);
        g.onMotion(4_500);                       // still the same burst
        assertEquals(12_000, g.intervalMs(N, false));
        g.onMotion(4_500 + PersonGate.EPISODE_GAP_MS);   // exactly the gap: still the same
        assertEquals(12_000, g.intervalMs(N, false));
        g.onMotion(4_500 + 2 * PersonGate.EPISODE_GAP_MS + 1); // after a long pause
        assertEquals(PersonGate.IDLE_CHECK_MS, g.intervalMs(N, false));
    }

    @Test
    public void thermalSlowdownAndBackOffCombineButNeverExceedTheCap() {
        PersonGate g = new PersonGate();
        for (int i = 0; i < 10; i++) g.onResult(i * 1000L, false);
        assertEquals(12_000, g.intervalMs(ThermalPolicy.Level.WARM, false));
        assertEquals(12_000, g.intervalMs(ThermalPolicy.Level.HOT, false));
        // the heat slowdown alone may exceed the cap: the back-off never shortens it
        assertEquals(PersonGate.checkIntervalMs(ThermalPolicy.Level.CRITICAL, false),
                new PersonGate().intervalMs(ThermalPolicy.Level.CRITICAL, false));
    }

    @Test
    public void wantCheckFollowsTheBackedOffInterval() {
        PersonGate g = new PersonGate();
        g.onMotion(100_000L);
        for (int i = 0; i < 10; i++) g.onResult(100_000L + i, false);
        long at = 100_009L; // the last (empty) check
        g.onMotion(at + 9_000);
        g.onMotion(at + 11_999);
        assertFalse(g.wantCheck(at + 11_999, false, N));
        g.onMotion(at + 12_000);
        assertTrue(g.wantCheck(at + 12_000, false, N));
    }

    /** Frames every 100 ms with motion all the time (a curtain in the wind) and never a person. */
    @Test
    public void aCurtainSwayingForTenMinutesCostsAtMostSixtyChecks() {
        PersonGate g = new PersonGate();
        int checks = 0;
        for (long t = 0; t < 600_000; t += 100) {
            g.onMotion(t);
            if (g.wantCheck(t, false, N)) {
                g.onResult(t, false, false);
                checks++;
            }
        }
        assertTrue("checks: " + checks, checks <= 60);
        assertTrue("it still looks regularly: " + checks, checks >= 40);
    }

    @Test
    public void theCurtainOnAWarmPhoneCostsEvenLess() {
        PersonGate g = new PersonGate();
        int checks = 0;
        for (long t = 0; t < 600_000; t += 100) {
            g.onMotion(t);
            if (g.wantCheck(t, false, ThermalPolicy.Level.HOT)) {
                g.onResult(t, false, false);
                checks++;
            }
        }
        assertTrue("checks: " + checks, checks <= 60);
    }

    @Test
    public void aPersonWalkingInAfterALongCurtainEpisodeIsStillRecordedPromptly() {
        PersonGate g = new PersonGate();
        long t = 0;
        for (; t < 120_000; t += 100) { // two minutes of curtain: fully backed off
            g.onMotion(t);
            if (g.wantCheck(t, false, N)) g.onResult(t, false, false);
        }
        assertEquals(12_000, g.intervalMs(N, false));
        // the person appears; the next check (at most 12 s away) finds them ...
        long firstHit = -1;
        long start = -1;
        for (; t < 120_000 + 40_000 && start < 0; t += 100) {
            g.onMotion(t);
            if (g.wantCheck(t, false, N)) {
                g.onResult(t, true, false);
                if (firstHit < 0) firstHit = t;
                if (g.shouldStart(t)) start = t;
            }
        }
        assertTrue(firstHit >= 0 && firstHit - 120_000 <= 12_000);
        // ... and the fast rate is back, so the second hit follows within half a second
        assertTrue("start at " + start, start >= 0 && start - firstHit <= PersonGate.IDLE_CHECK_MS);
    }

    @Test
    public void recordingChecksKeepTheirTwoSecondRhythmAfterALongBackOff() {
        PersonGate g = new PersonGate();
        for (int i = 0; i < 12; i++) g.onResult(i * 1000L, false);
        g.onRecordingStarted(20_000);
        g.onResult(20_000, true, true);
        assertFalse(g.wantCheck(21_999, true, N));
        assertTrue(g.wantCheck(22_000, true, N));
    }
}
