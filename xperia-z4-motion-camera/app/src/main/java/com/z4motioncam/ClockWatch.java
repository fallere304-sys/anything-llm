package com.z4motioncam;

/**
 * Notices that the wall clock was changed (by hand, by network time, or a time zone switch with a
 * different offset is a separate broadcast) by comparing how far the wall clock moved with how far
 * the monotonic clock moved. Used only to write a journal entry; the sleep-time logic reads the
 * clock afresh each time, so it needs no special handling.
 */
final class ClockWatch {
    /** Smaller differences are ordinary drift or a slow tick. */
    static final long TOLERANCE_MS = 2 * 60_000L;

    private long lastWall = -1;
    private long lastMono = -1;

    /** @return wall-clock jump in milliseconds (positive = the clock moved forward), or 0 when none */
    long observe(long wallMs, long monoMs) {
        long jump = 0;
        if (lastWall >= 0 && lastMono >= 0) {
            long diff = (wallMs - lastWall) - (monoMs - lastMono);
            if (Math.abs(diff) > TOLERANCE_MS) jump = diff;
        }
        lastWall = wallMs;
        lastMono = monoMs;
        return jump;
    }

    static String describe(long jumpMs) {
        long min = Math.abs(jumpMs) / 60_000L;
        String span = min >= 60 * 24 ? (min / (60 * 24)) + "日" + (min % (60 * 24) / 60) + "時間"
                : min >= 60 ? (min / 60) + "時間" + (min % 60) + "分" : min + "分";
        return "端末の時計が約" + span + (jumpMs > 0 ? "進みました" : "戻りました");
    }
}
