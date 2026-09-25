package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.File;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class UptimeLogTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    /** Controllable clock: wall time and time since boot. */
    static final class FakeClock implements UptimeLog.Clock {
        long wall = 1_790_000_000_000L;
        long boot = 3_600_000L;

        public long wallMs() { return wall; }
        public long sinceBootMs() { return boot; }

        void advanceMinutes(int m) {
            wall += m * 60_000L;
            boot += m * 60_000L;
        }
    }

    private static UptimeLog.Snapshot snap(int pct, float temp, String power) {
        UptimeLog.Snapshot s = new UptimeLog.Snapshot();
        s.batteryPct = pct;
        s.tempC = temp;
        s.voltageMv = 3700;
        s.power = power;
        s.health = "良好";
        s.thermal = "NORMAL";
        return s;
    }

    private static void run(UptimeLog log, FakeClock c, int minutes, UptimeLog.Snapshot s) {
        for (int i = 0; i < minutes; i++) {
            c.advanceMinutes(1);
            log.beat(s);
        }
    }

    @Test
    public void suddenPowerLossIsDetectedFromLastHeartbeat() {
        File dir = tmp.getRoot();
        FakeClock c = new FakeClock();
        UptimeLog log = new UptimeLog(dir, c);
        long start = c.wall;
        log.begin(snap(100, 30f, "満充電"));
        run(log, c, 185, snap(3, 44.5f, "接続中・充電停止")); // runs 3 h 05 min, then power dies
        long lastBeat = c.wall;

        // Phone is off for 4 hours, then boots: the since-boot counter restarts from ~0.
        c.wall += 4 * 3600_000L;
        c.boot = 40_000L;
        UptimeLog after = new UptimeLog(dir, c);
        after.begin(snap(100, 28f, "満充電"));
        String j = after.json();
        assertTrue(j, j.contains("\"start\":" + start + ",\"end\":" + lastBeat + ",\"minutes\":185"));
        assertTrue(j, j.contains(UptimeLog.END_POWER_LOSS));
        // The state right before it died is kept: battery 3 %, 44.5 C, charging stopped.
        assertTrue(j, j.contains("\"last\":{\"battery\":3,\"tempC\":44.5,\"voltageMv\":3700,\"power\":\"接続中・充電停止\""));
    }

    @Test
    public void appKilledWhilePhoneKeepsRunning() {
        File dir = tmp.getRoot();
        FakeClock c = new FakeClock();
        UptimeLog log = new UptimeLog(dir, c);
        log.begin(snap(80, 35f, "充電中"));
        run(log, c, 10, snap(80, 35f, "充電中"));
        c.advanceMinutes(2); // app restarted by Android two minutes later, no reboot
        UptimeLog again = new UptimeLog(dir, c);
        again.begin(snap(80, 35f, "充電中"));
        assertTrue(again.json().contains(UptimeLog.END_APP_KILLED));
        assertFalse(again.json().contains(UptimeLog.END_POWER_LOSS));
    }

    @Test
    public void cleanStopsAreRecordedOnce() {
        File dir = tmp.getRoot();
        FakeClock c = new FakeClock();
        UptimeLog log = new UptimeLog(dir, c);
        log.begin(snap(90, 33f, "充電中"));
        run(log, c, 30, snap(90, 33f, "充電中"));
        log.end(UptimeLog.END_SHUTDOWN, snap(90, 33f, "充電中"));
        log.end(UptimeLog.END_STOP, snap(90, 33f, "充電中")); // onDestroy after the shutdown notice
        c.boot = 10_000L;
        UptimeLog next = new UptimeLog(dir, c);
        next.begin(snap(90, 30f, "充電中"));
        String j = next.json();
        assertTrue(j, j.contains(UptimeLog.END_SHUTDOWN));
        assertFalse("closed session is not reported again", j.contains(UptimeLog.END_POWER_LOSS));
        assertFalse(j.contains(UptimeLog.END_STOP));
        assertTrue(j.contains("\"minutes\":30"));
    }

    @Test
    public void samplesEveryFiveMinutesAndCountsUptime() {
        FakeClock c = new FakeClock();
        UptimeLog log = new UptimeLog(tmp.getRoot(), c);
        log.begin(snap(50, 36f, "充電中"));
        run(log, c, 23, snap(55, 36.5f, "充電中"));
        assertEquals(23, log.minutes());
        String j = log.json();
        int samples = j.split("\"t\":", -1).length - 1;
        assertEquals(4, samples); // at 5, 10, 15, 20 minutes
        assertTrue(j.startsWith("{\"current\":{\"start\":"));
        assertTrue(j.endsWith("]}"));
    }
}
