package com.z4motioncam;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class KeepFrontTest {
    private static final KeepFront.Action NONE = KeepFront.Action.NONE;
    private static final KeepFront.Action RELAUNCH = KeepFront.Action.RELAUNCH;
    private static final KeepFront.Action PAUSE = KeepFront.Action.PAUSE;

    @Test
    public void nothingWhileTheAppIsShowing() {
        KeepFront k = new KeepFront();
        for (long t = 0; t < 600_000; t += 5_000) assertEquals(NONE, k.check(t, true, true));
    }

    @Test
    public void screenOffComesBackAfterFiveSeconds() {
        KeepFront k = new KeepFront();
        assertEquals(NONE, k.check(0, false, false));
        assertEquals(NONE, k.check(4_000, false, false));
        assertEquals(RELAUNCH, k.check(5_000, false, false));
        // Showing again resets the timer.
        assertEquals(NONE, k.check(10_000, true, true));
        assertEquals(NONE, k.check(15_000, false, false));
        assertEquals(RELAUNCH, k.check(20_000, false, false));
    }

    @Test
    public void anotherAppInFrontGetsAMinute() {
        KeepFront k = new KeepFront();
        assertEquals(NONE, k.check(0, false, true));
        assertEquals(NONE, k.check(55_000, false, true));
        assertEquals(RELAUNCH, k.check(60_000, false, true));
        // Still hidden right after the relaunch: waits again instead of retrying at once.
        assertEquals(NONE, k.check(65_000, false, true));
        assertEquals(RELAUNCH, k.check(120_000, false, true));
    }

    @Test
    public void backsOffWhenSomethingKeepsClosingIt() {
        KeepFront k = new KeepFront();
        long t = 0;
        int relaunches = 0;
        KeepFront.Action a;
        // Screen forced off again and again (e.g. a tablet's bedtime lock).
        while ((a = k.check(t, false, false)) != PAUSE) {
            if (a == RELAUNCH) relaunches++;
            t += 1_000;
        }
        assertEquals(KeepFront.MAX_RELAUNCHES, relaunches);
        long pausedAt = t;
        for (t = pausedAt + 1_000; t < pausedAt + KeepFront.PAUSE_MS; t += 5_000) {
            assertEquals(NONE, k.check(t, false, false));
        }
        // After the pause it tries again.
        boolean retried = false;
        for (; t < pausedAt + KeepFront.PAUSE_MS + 20_000; t += 1_000) retried |= k.check(t, false, false) == RELAUNCH;
        assertEquals(true, retried);
    }

    @Test
    public void occasionalHidingNeverPauses() {
        KeepFront k = new KeepFront();
        // Once every 5 minutes for a day: never more than 6 in 10 minutes.
        for (long t = 0; t < 24 * 3600_000L; t += 300_000) {
            assertEquals(NONE, k.check(t, false, false));
            assertEquals(RELAUNCH, k.check(t + 5_000, false, false));
            assertEquals(NONE, k.check(t + 6_000, true, true));
        }
    }
}
