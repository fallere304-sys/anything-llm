package com.z4motioncam;

/**
 * Person mode's recording rules. Motion is the cheap gatekeeper: the person check only runs while
 * there is (or just was) motion, so a quiet room costs nothing extra. Pure logic, driven by the
 * camera thread with its monotonic clock.
 *
 * <ul>
 *   <li>Start: at least {@link #NEED} of the last {@link #HISTORY} checks found a person and the
 *       latest one did (a single lucky detection is not enough).
 *   <li>While recording: a check every {@link #RECORD_CHECK_MS} even without motion (a person
 *       sitting still is still a person); every positive refreshes "last seen".
 *   <li>Stop: no person seen for the configured "keep recording" seconds.
 * </ul>
 */
final class PersonGate {
    static final int HISTORY = 3;
    static final int NEED = 2;
    /** Not recording: checks happen only this long after the last motion. */
    static final long MOTION_WINDOW_MS = 4_000L;
    static final long IDLE_CHECK_MS = 500L;
    static final long RECORD_CHECK_MS = 2_000L;
    /** Older results are forgotten: two hits must belong to one scene. */
    static final long HISTORY_MAX_GAP_MS = 5_000L;
    private static final long NEVER = Long.MIN_VALUE / 2;

    private final boolean[] history = new boolean[HISTORY];
    private int count;
    private long lastMotionMs = NEVER;
    private long lastCheckMs = NEVER;
    private long lastPersonMs = NEVER;
    private boolean everSeen;

    /** Minimum time between checks; slower when the phone is warm. */
    static long checkIntervalMs(ThermalPolicy.Level l, boolean recording) {
        long base = recording ? RECORD_CHECK_MS : IDLE_CHECK_MS;
        switch (l) {
            case NORMAL: return base;
            case WARM: return base * 2;
            default: return base * 4;
        }
    }

    void onMotion(long now) {
        lastMotionMs = now;
    }

    boolean wantCheck(long now, boolean recording, ThermalPolicy.Level l) {
        if (now - lastCheckMs < checkIntervalMs(l, recording)) return false;
        return recording || now - lastMotionMs <= MOTION_WINDOW_MS;
    }

    void onResult(long now, boolean person) {
        if (now - lastCheckMs > HISTORY_MAX_GAP_MS) count = 0;
        lastCheckMs = now;
        for (int i = Math.min(count, HISTORY - 1); i > 0; i--) history[i] = history[i - 1];
        history[0] = person;
        if (count < HISTORY) count++;
        if (person) {
            lastPersonMs = now;
            everSeen = true;
        }
    }

    boolean shouldStart(long now) {
        if (count == 0 || !history[0] || now - lastCheckMs > HISTORY_MAX_GAP_MS) return false;
        int hits = 0;
        for (int i = 0; i < count; i++) if (history[i]) hits++;
        return hits >= NEED;
    }

    /** Recording just began: count it as a sighting so it is not stopped before the first check. */
    void onRecordingStarted(long now) {
        lastPersonMs = now;
        everSeen = true;
    }

    boolean shouldStop(long now, long keepMs) {
        return !everSeen || now - lastPersonMs > keepMs;
    }

    /** Monotonic time of the last sighting, or -1. */
    long lastPersonMs() {
        return everSeen ? lastPersonMs : -1;
    }

    void reset() {
        count = 0;
        everSeen = false;
        lastMotionMs = NEVER;
        lastCheckMs = NEVER;
        lastPersonMs = NEVER;
    }
}
