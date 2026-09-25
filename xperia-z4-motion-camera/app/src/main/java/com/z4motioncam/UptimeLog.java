package com.z4motioncam;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.List;

/**
 * Records how long monitoring ran and how it ended, including sudden power loss.
 *
 * <p>A sudden power-off leaves no chance to write "stopped at", so a heartbeat is written every
 * minute instead: the last heartbeat is when it stopped (within a minute). On the next start an
 * unfinished session is closed from that heartbeat. Comparing the phone's time-since-boot tells a
 * power loss (counter reset) from the app alone being killed (counter kept running).
 *
 * <p>Files (app-private): current.txt (open session, rewritten each minute), sessions.txt (finished
 * sessions), samples.txt (battery every 5 minutes, 7 days). Plain tab-separated text.
 */
final class UptimeLog {
    static final String END_STOP = "停止ボタン（アプリ停止）";
    static final String END_SHUTDOWN = "端末のシャットダウン・再起動（電源メニュー、電池切れ、過熱など）";
    static final String END_POWER_LOSS = "突然の電源断（シャットダウン通知なし）";
    static final String END_APP_KILLED = "アプリだけが強制終了（端末は動作継続）";

    static final long BEAT_MS = 60_000L;
    private static final int SAMPLE_EVERY_BEATS = 5;
    private static final int MAX_SAMPLES = 7 * 24 * 12;
    private static final int MAX_SESSIONS = 200;
    private static final Charset UTF8 = Charset.forName("UTF-8");

    interface Clock {
        long wallMs();

        /** Monotonic time since the phone booted (SystemClock.elapsedRealtime). */
        long sinceBootMs();
    }

    /** Battery / load state at a heartbeat. */
    static final class Snapshot {
        int batteryPct = -1;
        float tempC = Float.NaN;
        int voltageMv = -1;
        /** e.g. 充電中 / 満充電 / 接続中・充電停止 / 未接続 */
        String power = "";
        /** e.g. 良好 / 過熱 */
        String health = "";
        String thermal = "";
        boolean recording;

        String[] fields() {
            return new String[] {String.valueOf(batteryPct), Float.isNaN(tempC) ? "" : String.valueOf(tempC),
                    String.valueOf(voltageMv), clean(power), clean(health), clean(thermal), recording ? "1" : "0"};
        }

        static Snapshot from(String[] f, int at) {
            Snapshot s = new Snapshot();
            if (f.length < at + 7) return s;
            s.batteryPct = parseInt(f[at], -1);
            s.tempC = f[at + 1].isEmpty() ? Float.NaN : parseFloat(f[at + 1]);
            s.voltageMv = parseInt(f[at + 2], -1);
            s.power = f[at + 3];
            s.health = f[at + 4];
            s.thermal = f[at + 5];
            s.recording = "1".equals(f[at + 6]);
            return s;
        }

        String json() {
            return "{\"battery\":" + batteryPct + ",\"tempC\":" + (Float.isNaN(tempC) ? "null" : String.valueOf(tempC))
                    + ",\"voltageMv\":" + voltageMv + ",\"power\":" + HttpServer.jsonString(power)
                    + ",\"health\":" + HttpServer.jsonString(health) + ",\"thermal\":" + HttpServer.jsonString(thermal)
                    + ",\"recording\":" + recording + "}";
        }
    }

    private final File current;
    private final File sessions;
    private final File samples;
    private final Clock clock;

    private long startWall;
    private long startBoot;
    private int beats;
    private boolean open;

    UptimeLog(File dir, Clock clock) {
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        this.current = new File(dir, "current.txt");
        this.sessions = new File(dir, "sessions.txt");
        this.samples = new File(dir, "samples.txt");
        this.clock = clock;
    }

    /** Closes a session left open by a sudden stop, then starts a new one. */
    synchronized void begin(Snapshot now) {
        List<String> cur = readLines(current);
        if (!cur.isEmpty()) {
            String[] f = cur.get(0).split("\t", -1);
            if (f.length >= 4) {
                long prevStartWall = parseLong(f[0]);
                long prevStartBoot = parseLong(f[1]);
                long lastWall = parseLong(f[2]);
                long lastBoot = parseLong(f[3]);
                // The since-boot counter only goes back when the phone itself restarted.
                String reason = clock.sinceBootMs() < lastBoot ? END_POWER_LOSS : END_APP_KILLED;
                appendSession(prevStartWall, lastWall, (lastBoot - prevStartBoot) / 60_000L, reason,
                        Snapshot.from(f, 4));
            }
        }
        startWall = clock.wallMs();
        startBoot = clock.sinceBootMs();
        beats = 0;
        open = true;
        writeCurrent(now);
        trim(samples, MAX_SAMPLES);
        trim(sessions, MAX_SESSIONS);
    }

    /** Called every {@link #BEAT_MS}. */
    synchronized void beat(Snapshot now) {
        if (!open) return;
        writeCurrent(now);
        if (++beats % SAMPLE_EVERY_BEATS == 0) {
            appendLine(samples, join(prefix(clock.wallMs()), now.fields()));
        }
    }

    /** Clean end (stop button or phone shutdown notice). */
    synchronized void end(String reason, Snapshot now) {
        if (!open) return;
        open = false;
        appendSession(startWall, clock.wallMs(), minutes(), reason, now);
        //noinspection ResultOfMethodCallIgnored
        current.delete();
    }

    synchronized long startWallMs() {
        return startWall;
    }

    /** Minutes since this session started (monotonic, unaffected by clock changes). */
    synchronized long minutes() {
        return (clock.sinceBootMs() - startBoot) / 60_000L;
    }

    synchronized String json() {
        StringBuilder sb = new StringBuilder("{\"current\":{\"start\":").append(startWall)
                .append(",\"minutes\":").append(minutes()).append("},\"sessions\":[");
        List<String> ses = readLines(sessions);
        boolean first = true;
        for (int i = ses.size() - 1; i >= 0; i--) { // newest first
            String[] f = ses.get(i).split("\t", -1);
            if (f.length < 4) continue;
            if (!first) sb.append(',');
            first = false;
            sb.append("{\"start\":").append(parseLong(f[0])).append(",\"end\":").append(parseLong(f[1]))
                    .append(",\"minutes\":").append(parseLong(f[2])).append(",\"reason\":")
                    .append(HttpServer.jsonString(f[3])).append(",\"last\":").append(Snapshot.from(f, 4).json())
                    .append('}');
        }
        sb.append("],\"samples\":[");
        List<String> smp = readLines(samples);
        long since = clock.wallMs() - 24 * 3600_000L; // last 24 hours on the page
        first = true;
        for (int i = smp.size() - 1; i >= 0; i--) {
            String[] f = smp.get(i).split("\t", -1);
            long t = parseLong(f[0]);
            if (t < since) break;
            if (!first) sb.append(',');
            first = false;
            String snap = Snapshot.from(f, 1).json();
            sb.append("{\"t\":").append(t).append(',').append(snap.substring(1));
        }
        return sb.append("]}").toString();
    }

    // ---- files ----

    private void writeCurrent(Snapshot now) {
        String line = join(new String[] {String.valueOf(startWall), String.valueOf(startBoot),
                String.valueOf(clock.wallMs()), String.valueOf(clock.sinceBootMs())}, now.fields());
        File tmp = new File(current.getPath() + ".tmp");
        try {
            OutputStream out = new FileOutputStream(tmp);
            try {
                out.write((line + "\n").getBytes(UTF8));
                // Must survive a sudden power cut: push it to the storage chip, not just the cache.
                ((FileOutputStream) out).getFD().sync();
            } finally {
                out.close();
            }
            if (!tmp.renameTo(current)) throw new IOException("rename failed");
        } catch (IOException ignored) {
            // best effort; the previous heartbeat stays
        }
    }

    private void appendSession(long start, long end, long minutes, String reason, Snapshot last) {
        appendLine(sessions, join(new String[] {String.valueOf(start), String.valueOf(end),
                String.valueOf(Math.max(0, minutes)), reason}, last.fields()));
    }

    private static void appendLine(File f, String line) {
        try {
            FileOutputStream out = new FileOutputStream(f, true);
            try {
                out.write((line + "\n").getBytes(UTF8));
                out.getFD().sync();
            } finally {
                out.close();
            }
        } catch (IOException ignored) {
            // best effort
        }
    }

    private static void trim(File f, int maxLines) {
        List<String> lines = readLines(f);
        if (lines.size() <= maxLines) return;
        StringBuilder sb = new StringBuilder();
        for (String l : lines.subList(lines.size() - maxLines, lines.size())) sb.append(l).append('\n');
        try {
            FileOutputStream out = new FileOutputStream(f);
            try {
                out.write(sb.toString().getBytes(UTF8));
            } finally {
                out.close();
            }
        } catch (IOException ignored) {
            // keep the long file
        }
    }

    private static List<String> readLines(File f) {
        List<String> out = new ArrayList<>();
        if (!f.isFile()) return out;
        try {
            BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), UTF8));
            try {
                String l;
                while ((l = r.readLine()) != null) {
                    if (!l.isEmpty()) out.add(l);
                }
            } finally {
                r.close();
            }
        } catch (IOException ignored) {
            // treat as empty
        }
        return out;
    }

    private static String[] prefix(long wall) {
        return new String[] {String.valueOf(wall)};
    }

    private static String join(String[] a, String[] b) {
        StringBuilder sb = new StringBuilder();
        for (String s : a) sb.append(s).append('\t');
        for (int i = 0; i < b.length; i++) {
            if (i > 0) sb.append('\t');
            sb.append(b[i]);
        }
        return sb.toString();
    }

    private static String clean(String s) {
        return s == null ? "" : s.replace('\t', ' ').replace('\n', ' ');
    }

    private static long parseLong(String s) {
        try {
            return Long.parseLong(s.trim());
        } catch (RuntimeException e) {
            return 0L;
        }
    }

    private static int parseInt(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (RuntimeException e) {
            return def;
        }
    }

    private static float parseFloat(String s) {
        try {
            return Float.parseFloat(s.trim());
        } catch (RuntimeException e) {
            return Float.NaN;
        }
    }
}
