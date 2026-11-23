package com.z4motioncam;

/**
 * When to look for an update on its own: about once a day, at most. After a failure the retry waits
 * longer each time (30 min, 1 h, 2 h ... up to 6 h) so a dead server is not asked every minute.
 * Times are wall-clock milliseconds; a stored time in the future (the clock was set back) counts as
 * "due" so the schedule cannot freeze for days.
 */
final class UpdateSchedule {
    static final long INTERVAL_MS = 24L * 3600_000L;
    static final long RETRY_BASE_MS = 30L * 60_000L;
    static final long RETRY_MAX_MS = 6L * 3600_000L;
    /** Wait after app start, so the first check does not compete with the camera starting. */
    static final long STARTUP_DELAY_MS = 2L * 60_000L;

    private UpdateSchedule() {}

    static long retryDelayMs(int failures) {
        long d = RETRY_BASE_MS;
        for (int i = 1; i < failures && d < RETRY_MAX_MS; i++) d *= 2;
        return Math.min(d, RETRY_MAX_MS);
    }

    /**
     * @param lastSuccess wall time of the last successful check, 0 = never
     * @param lastAttempt wall time of the last attempt (success or not), 0 = never
     * @param failures    consecutive failures since the last success
     */
    static boolean due(long now, long lastSuccess, long lastAttempt, int failures) {
        if (failures > 0) {
            if (lastAttempt > now) return true; // clock went back
            return now - lastAttempt >= retryDelayMs(failures);
        }
        if (lastSuccess <= 0) return true;
        if (lastSuccess > now) return true; // clock went back
        return now - lastSuccess >= INTERVAL_MS;
    }

    /** Wall time of the next automatic check (for display); 0 when one is due now. */
    static long nextMs(long now, long lastSuccess, long lastAttempt, int failures) {
        if (due(now, lastSuccess, lastAttempt, failures)) return 0;
        return failures > 0 ? lastAttempt + retryDelayMs(failures) : lastSuccess + INTERVAL_MS;
    }
}
