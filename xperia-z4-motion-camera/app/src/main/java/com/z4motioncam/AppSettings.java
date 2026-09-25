package com.z4motioncam;

import android.content.Context;
import android.content.SharedPreferences;
import android.preference.PreferenceManager;

/** Immutable snapshot of the user settings. Compare snapshots with {@link #equals} to detect changes. */
final class AppSettings {
    /** Backlight turns off after this much time without touch (requirement: 30 s). */
    static final long DIM_TIMEOUT_MS = 30_000L;

    final int width;
    final int height;
    final int fps;
    final int rotation;
    final int sensitivity;
    final int postRecordSec;
    final int segmentMin;
    final boolean useSdCard;
    final long minFreeBytes;
    final int port;
    final String password;
    final boolean autostart;
    final boolean remoteEnabled;
    final int remotePort;
    final boolean upnp;
    /** DuckDNS sub-domain without ".duckdns.org"; empty when unused. */
    final String ddnsDomain;
    final String ddnsToken;
    /** Global IP or host name to show in the outside URL; empty = use what the router reports. */
    final String externalHost;

    private AppSettings(SharedPreferences p, String defaultExternalHost) {
        int[] res = parseResolution(p.getString("resolution", "640x480"));
        width = res[0];
        height = res[1];
        fps = clamp(parseInt(p.getString("fps", "10"), 10), 1, 30);
        rotation = (parseInt(p.getString("rotation", "0"), 0) / 90 % 4) * 90;
        sensitivity = clamp(parseInt(p.getString("sensitivity", "2"), 2), 1, 3);
        postRecordSec = clamp(parseInt(p.getString("post_record_sec", "10"), 10), 1, 600);
        segmentMin = clamp(parseInt(p.getString("segment_min", "5"), 5), 1, 60);
        useSdCard = p.getBoolean("use_sd", true);
        minFreeBytes = clamp(parseInt(p.getString("min_free_mb", "500"), 500), 100, 100_000) * 1024L * 1024L;
        port = clamp(parseInt(p.getString("port", "8080"), 8080), 1024, 65535);
        password = p.getString("password", "");
        autostart = p.getBoolean("autostart", true);
        remoteEnabled = p.getBoolean("remote_enabled", false);
        remotePort = clamp(parseInt(p.getString("remote_port", "8443"), 8443), 1024, 65535);
        upnp = p.getBoolean("upnp", true);
        String d = p.getString("ddns_domain", "").trim().toLowerCase(java.util.Locale.US);
        if (d.endsWith(".duckdns.org")) d = d.substring(0, d.length() - ".duckdns.org".length());
        ddnsDomain = d.matches("[a-z0-9-]{1,63}") ? d : "";
        ddnsToken = p.getString("ddns_token", "").trim();
        String h = p.getString("external_host", defaultExternalHost).trim();
        externalHost = h.matches("[A-Za-z0-9.:-]{1,253}") ? h : "";
    }

    static AppSettings load(Context context) {
        return new AppSettings(PreferenceManager.getDefaultSharedPreferences(context),
                context.getString(R.string.default_external_host));
    }

    boolean ddnsConfigured() {
        return !ddnsDomain.isEmpty() && !ddnsToken.isEmpty();
    }

    /** Changed-area ratio (0..1) above which a frame counts as motion. */
    float motionAreaThreshold() {
        switch (sensitivity) {
            case 3: return 0.005f;
            case 1: return 0.04f;
            default: return 0.015f;
        }
    }

    /** "WxH" from the resolution list; anything else falls back to 640x480. */
    static int[] parseResolution(String s) {
        String[] allowed = {"320x240", "640x480", "960x720", "1280x720", "1280x960", "1920x1080"};
        for (String a : allowed) {
            if (a.equals(s)) {
                String[] wh = a.split("x");
                return new int[] {Integer.parseInt(wh[0]), Integer.parseInt(wh[1])};
            }
        }
        return new int[] {640, 480};
    }

    int bitrate() {
        // Low bitrates keep the encoder and storage writes cheap; enough for a mostly static scene.
        int pixels = width * height;
        int base = pixels <= 320 * 240 ? 400_000
                : pixels <= 640 * 480 ? 1_000_000
                : pixels <= 1280 * 720 ? 2_000_000
                : pixels <= 1280 * 960 ? 2_500_000 : 4_000_000;
        float scale = Math.max(0.5f, Math.min(2f, fps / 10f));
        return Math.round(base * scale);
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
    public boolean equals(Object o) {
        if (!(o instanceof AppSettings)) return false;
        AppSettings s = (AppSettings) o;
        return width == s.width && height == s.height && fps == s.fps && rotation == s.rotation
                && sensitivity == s.sensitivity && postRecordSec == s.postRecordSec
                && segmentMin == s.segmentMin && useSdCard == s.useSdCard
                && minFreeBytes == s.minFreeBytes && port == s.port
                && password.equals(s.password) && autostart == s.autostart
                && remoteEnabled == s.remoteEnabled && remotePort == s.remotePort && upnp == s.upnp
                && ddnsDomain.equals(s.ddnsDomain) && ddnsToken.equals(s.ddnsToken)
                && externalHost.equals(s.externalHost);
    }

    @Override
    public int hashCode() {
        return width * 31 + fps * 17 + port;
    }
}
