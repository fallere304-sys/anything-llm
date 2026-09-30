package com.z4motioncam;

import java.util.ArrayDeque;

/**
 * Decides when to bring the app back to the front: shortly after the screen went off, later after
 * another app or the home screen covered it (so the phone can still be used briefly). If the app
 * keeps being pushed away (e.g. a tablet's usage-time limit), it backs off for a while instead of
 * fighting it. Pure logic, driven by a periodic check in the service.
 */
final class KeepFront {
    static final long SCREEN_OFF_DELAY_MS = 5_000L;
    static final long BACKGROUND_DELAY_MS = 60_000L;
    static final int MAX_RELAUNCHES = 6;
    static final long WINDOW_MS = 10 * 60_000L;
    static final long PAUSE_MS = 30 * 60_000L;

    enum Action { NONE, RELAUNCH, PAUSE }

    private final ArrayDeque<Long> recent = new ArrayDeque<>();
    private long hiddenSince = -1;
    private long pausedUntil = -1;

    /**
     * @param now         monotonic time
     * @param visible     one of the app's screens is on display
     * @param interactive the screen is on
     */
    Action check(long now, boolean visible, boolean interactive) {
        if (visible) {
            hiddenSince = -1;
            return Action.NONE;
        }
        if (hiddenSince < 0) hiddenSince = now;
        if (now < pausedUntil) return Action.NONE;
        long delay = interactive ? BACKGROUND_DELAY_MS : SCREEN_OFF_DELAY_MS;
        if (now - hiddenSince < delay) return Action.NONE;
        while (!recent.isEmpty() && now - recent.peekFirst() > WINDOW_MS) recent.pollFirst();
        if (recent.size() >= MAX_RELAUNCHES) {
            recent.clear();
            pausedUntil = now + PAUSE_MS;
            hiddenSince = -1;
            return Action.PAUSE;
        }
        recent.addLast(now);
        hiddenSince = now; // give the relaunch time to show before trying again
        return Action.RELAUNCH;
    }
}
