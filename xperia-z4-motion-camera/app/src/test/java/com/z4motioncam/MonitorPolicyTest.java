package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.List;
import java.util.TimeZone;
import org.junit.Before;
import org.junit.Test;

public class MonitorPolicyTest {
    private static final TimeZone JST = TimeZone.getTimeZone("Asia/Tokyo");
    private static final long CONFIRM = MonitorPolicy.CONFIRM_MS;

    private MonitorPolicy policy;
    private long mono = 1_000_000L;
    private long wall;
    private TimeZone zone = JST;
    private boolean userOn = true;
    private boolean thermal;
    private boolean updating;
    private final List<String> journal = new ArrayList<>();

    private static long jst(String local) throws Exception {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
        f.setTimeZone(JST);
        return f.parse(local).getTime();
    }

    @Before
    public void setUp() throws Exception {
        policy = new MonitorPolicy(MonitorPolicy.Config.defaults());
        wall = jst("2026-09-28 12:00:00");
    }

    private MonitorPolicy.Decision eval(int pct) {
        MonitorPolicy.Input in = new MonitorPolicy.Input();
        in.monoMs = mono;
        in.wallMs = wall;
        in.zone = zone;
        in.userOn = userOn;
        in.thermalCritical = thermal;
        in.updating = updating;
        in.batteryPct = pct;
        MonitorPolicy.Decision d = policy.evaluate(in);
        journal.addAll(d.events);
        return d;
    }

    private void advance(long ms) {
        mono += ms;
        wall += ms;
    }

    /** Evaluate, wait past the confirmation time, evaluate again: the settled answer. */
    private MonitorPolicy.Decision settle(int pct) {
        eval(pct);
        advance(CONFIRM + 1);
        return eval(pct);
    }

    private void at(String local) throws Exception {
        long target = jst(local);
        long delta = target - wall;
        wall = target;
        mono += Math.abs(delta); // the monotonic clock only goes forward
    }

    private boolean journalHas(String part) {
        for (String e : journal) if (e.contains(part)) return true;
        return false;
    }

    // ---- low battery ----

    @Test
    public void runsNormally() {
        MonitorPolicy.Decision d = eval(60);
        assertTrue(d.active());
        assertNull(d.primary);
        assertNull(d.text);
        assertEquals("[]", d.reasonsJson());
    }

    @Test
    public void pausesBelow18AfterConfirmation() {
        assertTrue(eval(17).active()); // not yet: the reading must hold
        advance(CONFIRM - 1);
        assertTrue(eval(17).active());
        advance(2);
        MonitorPolicy.Decision d = eval(17);
        assertFalse(d.active());
        assertEquals(MonitorPolicy.Reason.LOW_BATTERY, d.primary);
        assertTrue(journalHas("17%"));
    }

    @Test
    public void exactly18IsNotLow() {
        assertTrue(settle(18).active());
    }

    @Test
    public void staysPausedUntil50() {
        assertFalse(settle(10).active());
        for (int pct : new int[] {18, 19, 30, 45, 49}) {
            assertFalse("at " + pct, settle(pct).active());
        }
        assertTrue(settle(50).active());
        assertTrue(journalHas("回復"));
    }

    @Test
    public void resumeAlsoNeedsAHeldReading() {
        settle(10);
        assertFalse(eval(55).active());
        advance(CONFIRM / 2);
        assertFalse(eval(55).active());
        advance(CONFIRM);
        assertTrue(eval(55).active());
    }

    @Test
    public void aSingleGlitchDoesNotPause() {
        eval(60);
        advance(1_000);
        assertTrue(eval(3).active()); // a one-off bad reading
        advance(1_000);
        assertTrue(eval(60).active());
        advance(CONFIRM * 2);
        assertTrue(eval(60).active()); // and the pending change was forgotten
    }

    @Test
    public void aSingleGlitchDoesNotResume() {
        settle(10);
        advance(1_000);
        assertFalse(eval(100).active());
        advance(1_000);
        assertFalse(eval(10).active());
        advance(CONFIRM * 2);
        assertFalse(eval(10).active());
    }

    @Test
    public void unknownBatteryNeverStartsAPause() {
        for (int pct : new int[] {-1, 101, -50, 1000}) {
            assertTrue("pct " + pct, settle(pct).active());
        }
    }

    @Test
    public void unknownBatteryKeepsTheCurrentPause() {
        settle(10);
        assertFalse(settle(-1).active());
        assertTrue(policy.lowLatched());
    }

    @Test
    public void unknownBatteryIsJournaledOnce() {
        for (int i = 0; i < 5; i++) {
            eval(-1);
            advance(15_000);
        }
        int n = 0;
        for (String e : journal) if (e.contains("読み取れません")) n++;
        assertEquals(1, n);
        eval(50);
        eval(-1);
        n = 0;
        for (String e : journal) if (e.contains("読み取れません")) n++;
        assertEquals("again after a good reading", 2, n);
    }

    @Test
    public void lowBatteryPauseCanBeTurnedOff() {
        policy.setConfig(new MonitorPolicy.Config(0, 50, true, 180, 240, 80));
        assertTrue(settle(2).active());
    }

    @Test
    public void disablingWhilePausedReleases() {
        settle(10);
        policy.setConfig(new MonitorPolicy.Config(0, 50, true, 180, 240, 80));
        assertTrue(eval(10).active());
        assertTrue(journalHas("無効"));
    }

    @Test
    public void customLevels() {
        policy.setConfig(new MonitorPolicy.Config(30, 60, true, 180, 240, 80));
        assertFalse(settle(29).active());
        assertFalse(settle(59).active());
        assertTrue(settle(60).active());
    }

    // ---- the user's own switch ----

    @Test
    public void manualOffIsItsOwnReason() {
        userOn = false;
        MonitorPolicy.Decision d = eval(90);
        assertFalse(d.active());
        assertEquals(MonitorPolicy.Reason.USER_OFF, d.primary);
        assertEquals("[\"user_off\"]", d.reasonsJson());
    }

    @Test
    public void batteryRecoveryDoesNotTurnTheUserSwitchBackOn() {
        userOn = false;
        settle(10);
        MonitorPolicy.Decision d = settle(90);
        assertFalse(d.active());
        assertEquals("[\"user_off\"]", d.reasonsJson());
    }

    @Test
    public void userTurningOnDuringLowBatteryStillWaitsForCharge() {
        settle(10);
        userOn = true;
        MonitorPolicy.Decision d = settle(30);
        assertFalse(d.active());
        assertEquals(MonitorPolicy.Reason.LOW_BATTERY, d.primary);
        assertTrue(settle(50).active());
    }

    @Test
    public void userOffAndLowBatteryAreBothRecorded() {
        userOn = false;
        MonitorPolicy.Decision d = settle(10);
        assertEquals("[\"user_off\",\"low_battery\"]", d.reasonsJson());
        userOn = true;
        d = eval(10);
        assertEquals("[\"low_battery\"]", d.reasonsJson());
    }

    // ---- restart ----

    @Test
    public void lowBatteryPauseSurvivesARestartBetween18And50() {
        settle(10);
        String saved = policy.serialize();
        MonitorPolicy after = new MonitorPolicy(MonitorPolicy.Config.defaults());
        after.restore(saved);
        policy = after;
        MonitorPolicy.Decision d = eval(30); // right away, no waiting
        assertFalse(d.active());
        assertEquals(MonitorPolicy.Reason.LOW_BATTERY, d.primary);
        assertTrue(settle(50).active());
    }

    @Test
    public void withoutASavedLatchAt30PercentItRuns() {
        policy.restore("");
        assertTrue(settle(30).active());
    }

    @Test
    public void garbageStateIsIgnored() {
        for (String junk : new String[] {null, "", "xyz", ";;;", "l=", "=1", "l=1;q=99", "q=abc", "l=1=2"}) {
            MonitorPolicy p = new MonitorPolicy(MonitorPolicy.Config.defaults());
            p.restore(junk);
            assertNotNull(p.serialize());
        }
        policy.restore("l=1;q=99");
        assertTrue(policy.lowLatched());
        assertFalse(policy.quietPaused());
    }

    @Test
    public void serializeRoundTrips() throws Exception {
        at("2026-09-28 03:10:00");
        settle(10);
        String s = policy.serialize();
        MonitorPolicy p = new MonitorPolicy(MonitorPolicy.Config.defaults());
        p.restore(s);
        assertEquals(s, p.serialize());
        assertTrue(p.lowLatched());
        assertTrue(p.quietPaused());
    }

    // ---- sleep time ----

    @Test
    public void pausesInsideTheWindowWhenBelow80() throws Exception {
        at("2026-09-28 03:00:00");
        MonitorPolicy.Decision d = eval(70);
        assertFalse(d.active());
        assertEquals(MonitorPolicy.Reason.QUIET, d.primary);
        assertTrue(d.text.contains("03:00〜04:00"));
        assertTrue(journalHas("充電に専念"));
    }

    @Test
    public void keepsRunningInsideTheWindowAt80OrMore() throws Exception {
        at("2026-09-28 03:30:00");
        assertTrue(eval(80).active());
        assertTrue(journalHas("監視を続けます"));
        journal.clear();
        assertTrue(eval(100).active());
        assertTrue(journal.isEmpty());
    }

    @Test
    public void windowBoundaries() throws Exception {
        at("2026-09-28 02:59:59");
        assertTrue(eval(50).active());
        at("2026-09-28 03:00:00");
        assertFalse(eval(50).active());
        at("2026-09-28 03:59:59");
        assertFalse(eval(50).active());
        at("2026-09-28 04:00:00");
        assertTrue(eval(50).active());
        assertTrue(journalHas("終わりました"));
    }

    @Test
    public void noFlappingAround80() throws Exception {
        at("2026-09-28 03:10:00");
        assertTrue(eval(85).active()); // entered high: keep monitoring
        for (int pct : new int[] {81, 80, 79, 80, 79, 78, 77, 76, 75}) {
            advance(60_000);
            assertTrue("at " + pct, settle(pct).active());
        }
        assertFalse("below 75 pauses", settle(74).active());
        for (int pct : new int[] {75, 76, 78, 79, 79}) {
            assertFalse("paused at " + pct, settle(pct).active());
        }
        assertTrue("80 resumes", settle(80).active());
        assertTrue("and 79 does not stop it again", settle(79).active());
        assertTrue(settle(76).active());
    }

    @Test
    public void enteringTheWindowAt79PausesButEnteringAt80Keeps() throws Exception {
        at("2026-09-28 03:00:00");
        assertFalse(eval(79).active());
        MonitorPolicy p = new MonitorPolicy(MonitorPolicy.Config.defaults());
        policy = p;
        assertTrue(eval(80).active());
    }

    @Test
    public void pausedInTheWindowResumesAt80() throws Exception {
        at("2026-09-28 03:00:00");
        assertFalse(eval(60).active());
        assertFalse(settle(79).active());
        assertTrue(settle(80).active());
        assertTrue(journalHas("再開"));
    }

    @Test
    public void windowAcrossMidnight() throws Exception {
        policy.setConfig(new MonitorPolicy.Config(18, 50, true, 23 * 60, 60, 80));
        at("2026-09-28 22:59:00");
        assertTrue(eval(50).active());
        at("2026-09-28 23:00:00");
        assertFalse(eval(50).active());
        at("2026-09-29 00:30:00");
        assertFalse(eval(50).active());
        at("2026-09-29 00:59:59");
        assertFalse(eval(50).active());
        at("2026-09-29 01:00:00");
        assertTrue(eval(50).active());
    }

    @Test
    public void midnightCrossingDoesNotRestartTheWindow() throws Exception {
        policy.setConfig(new MonitorPolicy.Config(18, 50, true, 23 * 60, 60, 80));
        at("2026-09-28 23:30:00");
        assertTrue(eval(85).active());
        journal.clear();
        at("2026-09-29 00:00:05"); // the date changes while monitoring continues
        assertTrue(eval(78).active());
        assertTrue("no new entry event", journal.isEmpty());
        assertTrue(policy.serialize().contains("q=1"));
    }

    @Test
    public void disabledSleepTimeNeverPauses() throws Exception {
        policy.setConfig(new MonitorPolicy.Config(18, 50, false, 180, 240, 80));
        at("2026-09-28 03:30:00");
        assertTrue(settle(30).active());
    }

    @Test
    public void emptyWindowNeverPauses() throws Exception {
        policy.setConfig(new MonitorPolicy.Config(18, 50, true, 180, 180, 80));
        at("2026-09-28 03:00:00");
        assertTrue(settle(30).active());
    }

    @Test
    public void leavingTheWindowReleasesTheLatch() throws Exception {
        at("2026-09-28 03:10:00");
        assertFalse(eval(40).active());
        assertTrue(policy.quietPaused());
        at("2026-09-28 04:00:00");
        assertTrue(eval(40).active());
        assertFalse(policy.quietPaused());
    }

    @Test
    public void unsetClockDisablesSleepTimeAndSaysSoOnce() throws Exception {
        wall = 5_000_000L; // 1970: a phone that booted without network time
        for (int i = 0; i < 3; i++) {
            assertTrue(eval(30).active());
            advance(15_000);
        }
        int n = 0;
        for (String e : journal) if (e.contains("時計が未設定")) n++;
        assertEquals(1, n);
    }

    @Test
    public void clockSetIntoTheWindowPausesAndOutReleases() throws Exception {
        at("2026-09-28 12:00:00");
        assertTrue(eval(40).active());
        at("2026-09-28 03:20:00"); // set back by hand
        assertFalse(eval(40).active());
        at("2026-09-28 12:00:00");
        assertTrue(eval(40).active());
    }

    @Test
    public void clockJumpForwardPastTheWholeWindow() throws Exception {
        at("2026-09-28 02:50:00");
        assertTrue(eval(40).active());
        at("2026-09-28 05:00:00"); // slept through it
        assertTrue(eval(40).active());
        assertFalse(policy.quietPaused());
    }

    @Test
    public void timeZoneChangeMovesTheWindow() throws Exception {
        at("2026-09-28 03:30:00"); // 18:30 UTC the day before
        assertFalse(eval(40).active());
        zone = TimeZone.getTimeZone("UTC");
        assertTrue("same instant, 18:30 in UTC", eval(40).active());
        zone = TimeZone.getTimeZone("Asia/Tokyo");
        assertFalse(eval(40).active());
    }

    @Test
    public void sleepStateIsRestoredAfterRestart() throws Exception {
        at("2026-09-28 03:10:00");
        assertTrue(eval(85).active());
        advance(60_000);
        assertTrue(settle(77).active()); // running, within the 5% band
        String saved = policy.serialize();

        MonitorPolicy fresh = new MonitorPolicy(MonitorPolicy.Config.defaults());
        policy = fresh; // a restart that lost the state would treat 77% as "entering below 80": paused
        assertFalse(eval(77).active());

        MonitorPolicy restored = new MonitorPolicy(MonitorPolicy.Config.defaults());
        restored.restore(saved);
        policy = restored;
        assertTrue("restored state keeps running", eval(77).active());
    }

    @Test
    public void unknownBatteryInTheWindowKeepsMonitoring() throws Exception {
        at("2026-09-28 03:10:00");
        assertTrue(eval(-1).active());
        assertTrue(settle(-1).active());
    }

    @Test
    public void batteryReadingsThatDipForAMomentInTheWindowAreIgnored() throws Exception {
        at("2026-09-28 03:10:00");
        assertTrue(eval(90).active());
        advance(1_000);
        assertTrue(eval(60).active());
        advance(1_000);
        assertTrue(eval(90).active());
        advance(CONFIRM * 2);
        assertTrue(eval(90).active());
    }

    // ---- combined reasons ----

    @Test
    public void priorityOrder() throws Exception {
        at("2026-09-28 03:10:00");
        userOn = false;
        updating = true;
        thermal = true;
        MonitorPolicy.Decision d = settle(10);
        assertEquals("[\"user_off\",\"updating\",\"thermal\",\"low_battery\",\"quiet\"]", d.reasonsJson());
        assertEquals(MonitorPolicy.Reason.USER_OFF, d.primary);
        userOn = true;
        assertEquals(MonitorPolicy.Reason.UPDATING, eval(10).primary);
        updating = false;
        assertEquals(MonitorPolicy.Reason.THERMAL, eval(10).primary);
        thermal = false;
        assertEquals(MonitorPolicy.Reason.LOW_BATTERY, eval(10).primary);
        at("2026-09-28 04:00:00");
        assertEquals(MonitorPolicy.Reason.LOW_BATTERY, eval(10).primary);
        assertTrue(settle(50).active());
    }

    @Test
    public void heatIsIndependentOfTheBatteryLatch() {
        settle(10);
        thermal = true;
        assertEquals("[\"thermal\",\"low_battery\"]", eval(10).reasonsJson());
        thermal = false;
        assertEquals("[\"low_battery\"]", eval(10).reasonsJson());
        assertTrue(settle(60).active());
    }

    @Test
    public void textListsEveryReason() throws Exception {
        at("2026-09-28 03:10:00");
        thermal = true;
        MonitorPolicy.Decision d = settle(10);
        assertTrue(d.text.contains("高温"));
        assertTrue(d.text.contains("18%未満"));
        assertTrue(d.text.contains("50%まで"));
        assertTrue(d.text.contains("おやすみ時間"));
    }

    @Test
    public void updatingStopsAndReleases() {
        updating = true;
        MonitorPolicy.Decision d = eval(90);
        assertFalse(d.active());
        assertEquals(MonitorPolicy.Reason.UPDATING, d.primary);
        updating = false;
        assertTrue(eval(90).active());
        assertTrue(journalHas("監視に戻ります"));
    }

    // ---- journal ----

    @Test
    public void noEventsWhileNothingChanges() {
        eval(60);
        journal.clear();
        for (int i = 0; i < 20; i++) {
            advance(15_000);
            eval(60);
        }
        assertTrue(journal.isEmpty());
    }

    @Test
    public void everyTransitionIsJournaledOnce() {
        eval(60);
        journal.clear();
        settle(10);
        int paused = 0;
        for (String e : journal) if (e.contains("一時停止")) paused++;
        assertTrue(paused >= 1);
        int before = journal.size();
        settle(10);
        settle(10);
        assertEquals(before, journal.size());
    }

    @Test
    public void startingWhileStoppedIsReported() {
        userOn = false;
        eval(60);
        assertTrue(journalHas("起動時の状態"));
    }

    @Test
    public void resumeIsJournaled() {
        userOn = false;
        eval(60);
        userOn = true;
        eval(60);
        assertTrue(journalHas("監視を再開しました"));
        assertTrue(journalHas("オンにしました"));
    }

    // ---- configuration ----

    @Test
    public void configParsesAndFallsBack() {
        MonitorPolicy.Config c = MonitorPolicy.Config.parse("20", "60", true, "23:30", "5:15", "70");
        assertEquals(20, c.lowPausePct);
        assertEquals(60, c.lowResumePct);
        assertEquals(23 * 60 + 30, c.quietStartMin);
        assertEquals(5 * 60 + 15, c.quietEndMin);
        assertEquals(70, c.quietKeepPct);
        MonitorPolicy.Config bad = MonitorPolicy.Config.parse("x", "", true, "25:00", "oops", "-");
        assertEquals(18, bad.lowPausePct);
        assertEquals(50, bad.lowResumePct);
        assertEquals(180, bad.quietStartMin);
        assertEquals(240, bad.quietEndMin);
        assertEquals(80, bad.quietKeepPct);
    }

    @Test
    public void configKeepsTheResumeLevelClearlyAbovePause() {
        MonitorPolicy.Config c = new MonitorPolicy.Config(40, 41, true, 180, 240, 80);
        assertEquals(45, c.lowResumePct);
        MonitorPolicy.Config d = new MonitorPolicy.Config(90, 10, true, 180, 240, 80);
        assertEquals(95, d.lowResumePct);
        MonitorPolicy.Config e = new MonitorPolicy.Config(500, 500, true, 180, 240, 500);
        assertEquals(90, e.lowPausePct);
        assertEquals(100, e.lowResumePct);
        assertEquals(100, e.quietKeepPct);
    }

    @Test
    public void configNormalizesMinutes() {
        MonitorPolicy.Config c = new MonitorPolicy.Config(18, 50, true, -60, 1500, 80);
        assertEquals(23 * 60, c.quietStartMin);
        assertEquals(60, c.quietEndMin);
    }

    @Test
    public void changingTheThresholdsTakesEffectOnTheNextEvaluation() {
        assertTrue(settle(25).active());
        policy.setConfig(new MonitorPolicy.Config(30, 60, true, 180, 240, 80));
        assertFalse(settle(25).active());
    }
}
