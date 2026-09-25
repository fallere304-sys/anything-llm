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

    private AppSettings(SharedPreferences p) {
        String res = p.getString("resolution", "640x480");
        if ("1280x720".equals(res)) {
            width = 1280;
            height = 720;
        } else {
            width = 640;
            height = 480;
        }
        fps = clamp(parseInt(p.getString("fps", "10"), 10), 5, 15);
        rotation = (parseInt(p.getString("rotation", "0"), 0) / 90 % 4) * 90;
        sensitivity = clamp(parseInt(p.getString("sensitivity", "2"), 2), 1, 3);
        postRecordSec = clamp(parseInt(p.getString("post_record_sec", "10"), 10), 1, 600);
        segmentMin = clamp(parseInt(p.getString("segment_min", "5"), 5), 1, 60);
        useSdCard = p.getBoolean("use_sd", true);
        minFreeBytes = clamp(parseInt(p.getString("min_free_mb", "500"), 500), 100, 100_000) * 1024L * 1024L;
        port = clamp(parseInt(p.getString("port", "8080"), 8080), 1024, 65535);
        password = p.getString("password", "");
        autostart = p.getBoolean("autostart", true);
    }

    static AppSettings load(Context context) {
        return new AppSettings(PreferenceManager.getDefaultSharedPreferences(context));
    }

    /** Changed-area ratio (0..1) above which a frame counts as motion. */
    float motionAreaThreshold() {
        switch (sensitivity) {
            case 3: return 0.005f;
            case 1: return 0.04f;
            default: return 0.015f;
        }
    }

    int bitrate() {
        // Low bitrates keep the encoder and storage writes cheap; enough for a mostly static scene.
        return width >= 1280 ? 2_000_000 : 1_000_000;
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
                && password.equals(s.password) && autostart == s.autostart;
    }

    @Override
    public int hashCode() {
        return width * 31 + fps * 17 + port;
    }
}
