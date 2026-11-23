package com.z4motioncam;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/**
 * The single place that decides whether monitoring (the camera) may run, and why not.
 *
 * <p>Several independent reasons can stop it. They never overwrite each other: the user's manual
 * switch stays what the user set, and low battery / sleep time are separate latched reasons. The
 * camera runs only while no reason is set. Priority only decides which reason is shown first.
 *
 * <p>Reasons, highest priority first: user off, update being installed, overheating (CRITICAL),
 * low battery, sleep time.
 *
 * <p>Low battery: pause when the battery falls below {@code lowPausePct}, resume at
 * {@code lowResumePct} or more (hysteresis, default 18 / 50).
 *
 * <p>Sleep time (charge only): inside the daily window the camera is off unless the battery is at
 * {@code quietKeepPct} or more. Once running it keeps running until the battery drops
 * {@link #QUIET_BAND_PCT} below that, so it does not flip around the threshold.
 *
 * <p>Robustness rules: a battery reading outside 0..100 counts as "unknown" and never starts a
 * pause (the camera is the more valuable thing to keep alive); battery-driven switches must hold
 * for {@link #CONFIRM_MS} (a sensor glitch cannot flip the camera); the sleep window is not applied
 * while the clock looks unset (before 2020); the clock and zone are read at every evaluation, so
 * changes of either are simply picked up. The latches survive a restart via {@link #serialize}.
 *
 * <p>Pure Java, driven by the service with explicit time, so it is unit-tested without Android.
 */
final class MonitorPolicy {
    enum Reason {
        USER_OFF("user_off"), UPDATING("updating"), THERMAL("thermal"), LOW_BATTERY("low_battery"), QUIET("quiet");

        final String key;

        Reason(String key) {
            this.key = key;
        }
    }

    static final int QUIET_BAND_PCT = 5;
    /** A battery-driven switch must be wanted continuously this long before it happens. */
    static final long CONFIRM_MS = 20_000L;
    /** Wall clocks before this are "not set yet" (a phone that just booted without network time). */
    static final long MIN_VALID_WALL_MS = 1_577_836_800_000L; // 2020-01-01

    static final class Config {
        /** Pause below this percentage; 0 turns the low-battery pause off. */
        final int lowPausePct;
        final int lowResumePct;
        final boolean quietEnabled;
        final int quietStartMin;
        final int quietEndMin;
        /** In the sleep window, keep monitoring while the battery is at least this high. */
        final int quietKeepPct;

        Config(int lowPausePct, int lowResumePct, boolean quietEnabled, int quietStartMin, int quietEndMin,
               int quietKeepPct) {
            int pause = clamp(lowPausePct, 0, 90);
            this.lowPausePct = pause;
            // The resume level must lie clearly above the pause level, or the hysteresis is meaningless.
            this.lowResumePct = pause == 0 ? clamp(lowResumePct, 1, 100) : clamp(lowResumePct, Math.min(100, pause + 5), 100);
            this.quietEnabled = quietEnabled;
            this.quietStartMin = ((quietStartMin % QuietHours.DAY_MIN) + QuietHours.DAY_MIN) % QuietHours.DAY_MIN;
            this.quietEndMin = ((quietEndMin % QuietHours.DAY_MIN) + QuietHours.DAY_MIN) % QuietHours.DAY_MIN;
            this.quietKeepPct = clamp(quietKeepPct, QUIET_BAND_PCT + 1, 100);
        }

        static Config defaults() {
            return new Config(18, 50, true, 3 * 60, 4 * 60, 80);
        }

        /** Parses the preference strings; anything invalid falls back to the default. */
        static Config parse(String lowPause, String lowResume, boolean quietOn, String start, String end, String keep) {
            Config d = defaults();
            return new Config(parseInt(lowPause, d.lowPausePct), parseInt(lowResume, d.lowResumePct), quietOn,
                    QuietHours.parseHm(start, d.quietStartMin), QuietHours.parseHm(end, d.quietEndMin),
                    parseInt(keep, d.quietKeepPct));
        }

        String quietLabel() {
            return QuietHours.label(quietStartMin, quietEndMin);
        }
    }

    /** Everything the decision depends on, sampled by the caller. */
    static final class Input {
        /** Monotonic time (SystemClock.elapsedRealtime); never jumps with clock changes. */
        long monoMs;
        long wallMs;
        TimeZone zone = TimeZone.getDefault();
        boolean userOn = true;
        boolean thermalCritical;
        boolean updating;
        /** 0..100, anything else = unknown. */
        int batteryPct = -1;
    }

    static final class Decision {
        final EnumSet<Reason> reasons;
        final Reason primary;
        /** Japanese sentence for the status screen / browser; null while monitoring runs. */
        final String text;
        /** Journal entries (Japanese) for the transitions this evaluation caused. */
        final List<String> events;

        Decision(EnumSet<Reason> reasons, String text, List<String> events) {
            this.reasons = reasons;
            Reason p = null;
            for (Reason r : Reason.values()) {
                if (reasons.contains(r)) {
                    p = r;
                    break;
                }
            }
            this.primary = p;
            this.text = text;
            this.events = events;
        }

        boolean active() {
            return reasons.isEmpty();
        }

        String reasonsJson() {
            StringBuilder sb = new StringBuilder("[");
            boolean first = true;
            for (Reason r : Reason.values()) {
                if (!reasons.contains(r)) continue;
                if (!first) sb.append(',');
                first = false;
                sb.append('"').append(r.key).append('"');
            }
            return sb.append(']').toString();
        }
    }

    private static final int Q_OUT = 0;
    private static final int Q_KEEP = 1;
    private static final int Q_PAUSED = 2;

    /** Requires a wanted change to persist for CONFIRM_MS before it is applied. */
    private static final class Pending {
        boolean target;
        long since = -1;

        boolean apply(boolean current, boolean want, long now) {
            if (want == current) {
                since = -1;
                return current;
            }
            if (since < 0 || target != want || now < since) {
                target = want;
                since = now;
            }
            if (now - since >= CONFIRM_MS) {
                since = -1;
                return want;
            }
            return current;
        }

        void reset() {
            since = -1;
        }
    }

    private Config cfg;
    private boolean low;
    private int quiet = Q_OUT;
    private final Pending lowPending = new Pending();
    private final Pending quietPending = new Pending();
    private EnumSet<Reason> last;
    private boolean warnedBattery;
    private boolean warnedClock;

    MonitorPolicy(Config cfg) {
        this.cfg = cfg;
    }

    void setConfig(Config c) {
        cfg = c;
    }

    Config config() {
        return cfg;
    }

    /** "l=1;q=2": low-battery latch and sleep-window state, to survive a restart. */
    String serialize() {
        return "l=" + (low ? 1 : 0) + ";q=" + quiet;
    }

    /** Restores {@link #serialize} output; malformed text leaves the clean state. */
    void restore(String s) {
        low = false;
        quiet = Q_OUT;
        if (s == null) return;
        for (String part : s.split(";")) {
            String[] kv = part.split("=");
            if (kv.length != 2) continue;
            if (kv[0].equals("l")) low = kv[1].equals("1");
            if (kv[0].equals("q")) {
                int q = parseInt(kv[1], Q_OUT);
                quiet = q == Q_KEEP || q == Q_PAUSED ? q : Q_OUT;
            }
        }
        lowPending.reset();
        quietPending.reset();
    }

    boolean lowLatched() {
        return low;
    }

    boolean quietPaused() {
        return quiet == Q_PAUSED;
    }

    Decision evaluate(Input in) {
        List<String> events = new ArrayList<>();
        int pct = in.batteryPct;
        boolean known = pct >= 0 && pct <= 100;
        if (!known && !warnedBattery) {
            warnedBattery = true;
            events.add("電池残量を読み取れません。電池による一時停止は判定せず、今の状態を保ちます");
        } else if (known) {
            warnedBattery = false;
        }

        // ---- low battery ----
        if (cfg.lowPausePct <= 0) {
            if (low) events.add("電池が少ないときの一時停止が無効になったため、解除しました");
            low = false;
            lowPending.reset();
        } else if (known) {
            boolean want = low ? pct < cfg.lowResumePct : pct < cfg.lowPausePct;
            boolean now = lowPending.apply(low, want, in.monoMs);
            if (now != low) {
                low = now;
                events.add(low
                        ? "電池が" + pct + "%に下がったため、監視を一時停止しました（" + cfg.lowResumePct + "%まで充電して再開します）"
                        : "電池が" + pct + "%に回復したため、電池による一時停止を解除しました");
            }
        }

        // ---- sleep time ----
        boolean trusted = in.wallMs >= MIN_VALID_WALL_MS;
        if (cfg.quietEnabled && !trusted && !warnedClock) {
            warnedClock = true;
            events.add("端末の時計が未設定のようなので、おやすみ時間は適用しません");
        } else if (trusted) {
            warnedClock = false;
        }
        boolean inWindow = cfg.quietEnabled && trusted
                && QuietHours.contains(QuietHours.localMinuteOfDay(in.wallMs, in.zone), cfg.quietStartMin, cfg.quietEndMin);
        if (!inWindow) {
            if (quiet != Q_OUT) {
                events.add(cfg.quietEnabled && trusted
                        ? "おやすみ時間（" + cfg.quietLabel() + "）が終わりました"
                        : "おやすみ時間の設定または時計が変わったため、おやすみ時間の扱いを解除しました");
            }
            quiet = Q_OUT;
            quietPending.reset();
        } else if (quiet == Q_OUT) {
            // Entering the window: decided by the battery right now.
            if (!known || pct >= cfg.quietKeepPct) {
                quiet = Q_KEEP;
                events.add("おやすみ時間（" + cfg.quietLabel() + "）に入りました。"
                        + (known ? "電池が" + pct + "%（" + cfg.quietKeepPct + "%以上）なので監視を続けます"
                                : "電池残量が不明なので監視を続けます"));
            } else {
                quiet = Q_PAUSED;
                events.add("おやすみ時間（" + cfg.quietLabel() + "）に入りました。電池が" + pct
                        + "%（" + cfg.quietKeepPct + "%未満）なので、監視を止めて充電に専念します");
            }
            quietPending.reset();
        } else if (known) {
            boolean paused = quiet == Q_PAUSED;
            boolean want = paused ? pct < cfg.quietKeepPct : pct < cfg.quietKeepPct - QUIET_BAND_PCT;
            boolean now = quietPending.apply(paused, want, in.monoMs);
            if (now != paused) {
                quiet = now ? Q_PAUSED : Q_KEEP;
                events.add(now
                        ? "おやすみ時間中に電池が" + pct + "%に下がったため、監視を止めて充電に専念します"
                        : "おやすみ時間中ですが電池が" + pct + "%になったため、監視を再開します");
            }
        }

        EnumSet<Reason> reasons = EnumSet.noneOf(Reason.class);
        if (!in.userOn) reasons.add(Reason.USER_OFF);
        if (in.updating) reasons.add(Reason.UPDATING);
        if (in.thermalCritical) reasons.add(Reason.THERMAL);
        if (low) reasons.add(Reason.LOW_BATTERY);
        if (quiet == Q_PAUSED) reasons.add(Reason.QUIET);

        // Journal the reasons that the latches above did not already describe.
        if (last == null) {
            if (!reasons.isEmpty()) events.add("起動時の状態: 監視は停止中です（" + textFor(reasons) + "）");
        } else {
            for (Reason r : Reason.values()) {
                boolean was = last.contains(r);
                boolean is = reasons.contains(r);
                if (was == is) continue;
                String e = simpleEvent(r, is);
                if (e != null) events.add(e);
            }
            if (!last.isEmpty() && reasons.isEmpty()) events.add("監視を再開しました");
            else if (last.isEmpty() && !reasons.isEmpty()) events.add("監視を止めました（" + textFor(reasons) + "）");
        }
        last = reasons;
        return new Decision(reasons, reasons.isEmpty() ? null : textFor(reasons), events);
    }

    private String simpleEvent(Reason r, boolean on) {
        switch (r) {
            case USER_OFF: return on ? "手動で監視をオフにしました" : "手動で監視をオンにしました";
            case THERMAL: return on ? "高温（" + (int) ThermalPolicy.CRITICAL_C + "℃以上）のため、カメラを止めました"
                    : "温度が下がったため、高温による停止を解除しました";
            case UPDATING: return on ? "アプリの更新を導入するため、監視を止めました"
                    : "更新の導入画面が閉じられたため（または時間切れ）、監視に戻ります";
            default: return null; // low battery / sleep time are journaled where they change
        }
    }

    /** The highest-priority reason first, then the rest in brackets. */
    String textFor(EnumSet<Reason> reasons) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Reason r : Reason.values()) {
            if (!reasons.contains(r)) continue;
            if (!first) sb.append(" / ");
            first = false;
            sb.append(describe(r));
        }
        return sb.toString();
    }

    private String describe(Reason r) {
        switch (r) {
            case USER_OFF: return "手動でオフにされています";
            case UPDATING: return "アプリの更新を導入中です";
            case THERMAL: return "高温のため一時停止中（冷却待ち）";
            case LOW_BATTERY: return "電池が少ないため一時停止中（" + cfg.lowPausePct + "%未満。" + cfg.lowResumePct
                    + "%まで充電して再開）";
            default: return "おやすみ時間（" + cfg.quietLabel() + "）のため充電に専念中（電池" + cfg.quietKeepPct
                    + "%以上なら監視を続けます）";
        }
    }

    private static int parseInt(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (RuntimeException e) {
            return def;
        }
    }

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    @Override
    public String toString() {
        return String.format(Locale.US, "MonitorPolicy[%s]", serialize());
    }
}
