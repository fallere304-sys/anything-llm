package com.z4motioncam;

import java.util.Locale;
import java.util.TimeZone;

/**
 * The daily "sleep time" window as minutes since local midnight. Pure Java; a window may wrap past
 * midnight (23:00-01:00). Start equal to end means "no window" (never active), not "all day".
 * (No Math.floorMod: it needs API 24, the app supports API 21.)
 */
final class QuietHours {
    static final int DAY_MIN = 24 * 60;

    private QuietHours() {}

    /** "3:00" / "03:00" / "3:5" -> minutes since midnight, or {@code def} for anything else. */
    static int parseHm(String s, int def) {
        if (s == null) return def;
        String t = s.trim();
        int c = t.indexOf(':');
        if (c < 1 || c == t.length() - 1) return def;
        try {
            String hs = t.substring(0, c);
            String ms = t.substring(c + 1);
            if (hs.length() > 2 || ms.length() > 2) return def;
            int h = Integer.parseInt(hs);
            int m = Integer.parseInt(ms);
            if (h < 0 || h > 23 || m < 0 || m > 59) return def;
            return h * 60 + m;
        } catch (NumberFormatException e) {
            return def;
        }
    }

    static boolean valid(String s) {
        return parseHm(s, -1) >= 0;
    }

    static String format(int minuteOfDay) {
        int m = ((minuteOfDay % DAY_MIN) + DAY_MIN) % DAY_MIN;
        return String.format(Locale.US, "%02d:%02d", m / 60, m % 60);
    }

    /** True when {@code minute} is inside [start, end). Wraps past midnight when end < start. */
    static boolean contains(int minute, int start, int end) {
        if (start == end) return false;
        if (start < end) return minute >= start && minute < end;
        return minute >= start || minute < end;
    }

    /** Minutes since local midnight for a wall-clock time in the given zone (offset at that instant). */
    static int localMinuteOfDay(long wallMs, TimeZone tz) {
        long local = wallMs + tz.getOffset(wallMs);
        long minutes = local / 60_000L;
        if (local % 60_000L < 0) minutes--; // floor for times before 1970
        long m = minutes % DAY_MIN;
        if (m < 0) m += DAY_MIN;
        return (int) m;
    }

    static String label(int start, int end) {
        return format(start) + "〜" + format(end);
    }
}
