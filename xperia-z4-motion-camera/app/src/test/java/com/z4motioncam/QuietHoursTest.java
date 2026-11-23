package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.text.SimpleDateFormat;
import java.util.TimeZone;
import org.junit.Test;

public class QuietHoursTest {
    private static long wall(String local, String zone) throws Exception {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd HH:mm");
        f.setTimeZone(TimeZone.getTimeZone(zone));
        return f.parse(local).getTime();
    }

    @Test
    public void parsesTimes() {
        assertEquals(180, QuietHours.parseHm("03:00", -1));
        assertEquals(180, QuietHours.parseHm("3:00", -1));
        assertEquals(185, QuietHours.parseHm(" 3:05 ", -1));
        assertEquals(0, QuietHours.parseHm("0:00", -1));
        assertEquals(23 * 60 + 59, QuietHours.parseHm("23:59", -1));
    }

    @Test
    public void rejectsBadTimes() {
        for (String bad : new String[] {null, "", "3", "24:00", "03:60", "-1:00", "3:", ":30", "ab:cd", "003:00", "3:000", "3.00"}) {
            assertEquals(bad, -7, QuietHours.parseHm(bad, -7));
        }
        assertFalse(QuietHours.valid("25:00"));
        assertTrue(QuietHours.valid("04:00"));
    }

    @Test
    public void formatsAndWraps() {
        assertEquals("03:00", QuietHours.format(180));
        assertEquals("00:00", QuietHours.format(1440));
        assertEquals("23:59", QuietHours.format(-1));
        assertEquals("03:00〜04:00", QuietHours.label(180, 240));
    }

    @Test
    public void simpleWindowIsStartInclusiveEndExclusive() {
        assertFalse(QuietHours.contains(179, 180, 240));
        assertTrue(QuietHours.contains(180, 180, 240));
        assertTrue(QuietHours.contains(239, 180, 240));
        assertFalse(QuietHours.contains(240, 180, 240));
    }

    @Test
    public void windowCanWrapPastMidnight() {
        int start = 23 * 60;
        int end = 60;
        assertFalse(QuietHours.contains(22 * 60 + 59, start, end));
        assertTrue(QuietHours.contains(23 * 60, start, end));
        assertTrue(QuietHours.contains(23 * 60 + 59, start, end));
        assertTrue(QuietHours.contains(0, start, end));
        assertTrue(QuietHours.contains(59, start, end));
        assertFalse(QuietHours.contains(60, start, end));
        assertFalse(QuietHours.contains(12 * 60, start, end));
    }

    @Test
    public void equalStartAndEndMeansNoWindow() {
        for (int m = 0; m < 1440; m += 37) assertFalse(QuietHours.contains(m, 180, 180));
    }

    @Test
    public void localMinuteUsesTheZone() throws Exception {
        long t = wall("2026-09-28 03:30", "Asia/Tokyo");
        assertEquals(3 * 60 + 30, QuietHours.localMinuteOfDay(t, TimeZone.getTimeZone("Asia/Tokyo")));
        assertEquals(18 * 60 + 30, QuietHours.localMinuteOfDay(t, TimeZone.getTimeZone("UTC")));
    }

    @Test
    public void localMinuteBeforeEpochIsStillInRange() {
        for (long t = -86_400_000L * 3; t < 0; t += 7_777_777L) {
            int m = QuietHours.localMinuteOfDay(t, TimeZone.getTimeZone("UTC"));
            assertTrue(m >= 0 && m < 1440);
        }
        assertEquals(23 * 60 + 59, QuietHours.localMinuteOfDay(-1L, TimeZone.getTimeZone("UTC")));
    }

    @Test
    public void followsDaylightSavingOffsetsOfTheDate() throws Exception {
        // New York: 02:30 local on a winter day and on a summer day are different UTC instants.
        TimeZone ny = TimeZone.getTimeZone("America/New_York");
        long winter = wall("2026-01-15 03:30", "America/New_York");
        long summer = wall("2026-07-15 03:30", "America/New_York");
        assertEquals(3 * 60 + 30, QuietHours.localMinuteOfDay(winter, ny));
        assertEquals(3 * 60 + 30, QuietHours.localMinuteOfDay(summer, ny));
    }
}
