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
 *   <li>Back-off: while not recording, every check that finds nobody ({@link #BACKOFF_AFTER} in a
 *       row, then each one) doubles the wait until the next one, up to {@link #BACKOFF_MAX_MS}. So
 *       something that keeps moving but is not a person (a curtain in the wind, a fan) costs a
 *       handful of checks a minute instead of two a second. A person found, a recording, or a new
 *       burst of motion after a pause ({@link #EPISODE_GAP_MS}) brings the fast rate back.
 * </ul>
 */
final class PersonGate {
    static final int HISTORY = 3;
    static final int NEED = 2;
    /** Not recording: checks happen only this long after the last motion. */
    static final long MOTION_WINDOW_MS = 4_000L;
    static final long IDLE_CHECK_MS = 500L;
    static final long RECORD_CHECK_MS = 2_000L;
    /** Empty checks in a row (not recording) before the wait starts doubling. */
    static final int BACKOFF_AFTER = 3;
    /** The longest wait between checks while backing off. */
    static final long BACKOFF_MAX_MS = 12_000L;
    /** A pause in motion longer than this ends the episode: the next motion starts at the fast rate again. */
    static final long EPISODE_GAP_MS = 10_000L;
    /** Older results are forgotten: two hits must belong to one scene. */
    static final long HISTORY_MAX_GAP_MS = 5_000L;
    private static final long NEVER = Long.MIN_VALUE / 2;

    private final boolean[] history = new boolean[HISTORY];
    private int count;
    private long lastMotionMs = NEVER;
    private long lastCheckMs = NEVER;
    private long lastPersonMs = NEVER;
    private boolean everSeen;
    private int emptyChecks;

    /** Minimum time between checks; slower when the phone is warm. */
    static long checkIntervalMs(ThermalPolicy.Level l, boolean recording) {
        long base = recording ? RECORD_CHECK_MS : IDLE_CHECK_MS;
        switch (l) {
            case NORMAL: return base;
            case WARM: return base * 2;
            default: return base * 4;
        }
    }

    /** The wait that applies now: {@link #checkIntervalMs} plus the back-off after many empty checks. */
    long intervalMs(ThermalPolicy.Level l, boolean recording) {
        long base = checkIntervalMs(l, recording);
        if (recording) return base;
        long cap = Math.max(base, BACKOFF_MAX_MS);
        long iv = base;
        for (int i = emptyChecks - BACKOFF_AFTER + 1; i > 0 && iv < cap; i--) iv *= 2;
        return Math.min(iv, cap);
    }

    int emptyChecks() {
        return emptyChecks;
    }

    void onMotion(long now) {
        if (now - lastMotionMs > EPISODE_GAP_MS) emptyChecks = 0;
        lastMotionMs = now;
    }

    boolean wantCheck(long now, boolean recording, ThermalPolicy.Level l) {
        if (now - lastCheckMs < intervalMs(l, recording)) return false;
        return recording || now - lastMotionMs <= MOTION_WINDOW_MS;
    }

    void onResult(long now, boolean person) {
        onResult(now, person, false);
    }

    /** @param recording the check was made while recording (the back-off only counts idle checks) */
    void onResult(long now, boolean person, boolean recording) {
        if (person || recording) emptyChecks = 0;
        else if (emptyChecks < 1000) emptyChecks++;
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
        emptyChecks = 0;
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
        emptyChecks = 0;
        everSeen = false;
        lastMotionMs = NEVER;
        lastCheckMs = NEVER;
        lastPersonMs = NEVER;
    }
}
