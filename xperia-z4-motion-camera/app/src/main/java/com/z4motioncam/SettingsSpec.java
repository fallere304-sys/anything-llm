package com.z4motioncam;

import java.util.HashMap;
import java.util.Map;

/**
 * The settings that can be changed from the browser, with their allowed values. Pure Java so the
 * validation is unit-tested; labels come from the Android resources (see {@link SettingsApi}).
 * List values must match the *_values arrays in res/values/strings.xml (checked by a test).
 */
final class SettingsSpec {
    enum Type { LIST, INT, BOOL, TEXT, SECRET }

    static final class Field {
        final String key;
        final Type type;
        final String def;
        final String[] values;
        final int min;
        final int max;
        /** Changing it can cut the viewer's current connection. */
        final boolean connection;

        Field(String key, Type type, String def, String[] values, int min, int max, boolean connection) {
            this.key = key;
            this.type = type;
            this.def = def;
            this.values = values;
            this.min = min;
            this.max = max;
            this.connection = connection;
        }
    }

    private static Field list(String key, String def, String... values) {
        return new Field(key, Type.LIST, def, values, 0, 0, false);
    }

    static final Field[] FIELDS = {
            list("resolution", "640x480", "320x240", "640x480", "960x720", "1280x720", "1280x960", "1920x1080"),
            list("fps", "10", "1", "2", "3", "5", "7", "10", "15", "20", "24", "30"),
            list("rotation", "0", "0", "90", "180", "270"),
            list("sensitivity", "2", "1", "2", "3"),
            list("detect_mode", "motion", "motion", "person"),
            list("person_sens", "2", "1", "2", "3"),
            list("post_record_sec", "10", "5", "10", "20", "30", "60", "90", "120", "180"),
            list("segment_min", "0", "1", "5", "10", "15", "30", "60", "0"),
            new Field("timestamp", Type.BOOL, "true", null, 0, 0, false),
            new Field("use_sd", Type.BOOL, "true", null, 0, 0, false),
            new Field("min_free_mb", Type.INT, "500", null, 100, 100_000, false),
            new Field("port", Type.INT, "8080", null, 1024, 65535, true),
            new Field("remote_enabled", Type.BOOL, "false", null, 0, 0, true),
            new Field("password", Type.SECRET, "", null, 0, 0, true),
            new Field("external_host", Type.TEXT, "", null, 0, 0, true),
            new Field("remote_port", Type.INT, "8443", null, 1024, 65535, true),
            new Field("upnp", Type.BOOL, "true", null, 0, 0, false),
            new Field("ddns_domain", Type.TEXT, "", null, 0, 0, false),
            new Field("ddns_token", Type.SECRET, "", null, 0, 0, false),
            new Field("acme", Type.BOOL, "false", null, 0, 0, true),
            new Field("autostart", Type.BOOL, "true", null, 0, 0, false),
            new Field("keep_front", Type.BOOL, "true", null, 0, 0, false),
            new Field("low_batt_pause", Type.INT, "18", null, 0, 50, false),
            new Field("low_batt_resume", Type.INT, "50", null, 10, 100, false),
            new Field("quiet_enabled", Type.BOOL, "true", null, 0, 0, false),
            new Field("quiet_start", Type.TEXT, "03:00", null, 0, 0, false),
            new Field("quiet_end", Type.TEXT, "04:00", null, 0, 0, false),
            new Field("quiet_keep_pct", Type.INT, "80", null, 20, 100, false),
            new Field("update_auto", Type.BOOL, "true", null, 0, 0, false),
            new Field("update_url", Type.TEXT, "", null, 0, 0, false),
            new Field("update_sha256", Type.TEXT, "", null, 0, 0, false),
    };

    private SettingsSpec() {}

    static Field find(String key) {
        for (Field f : FIELDS) {
            if (f.key.equals(key)) return f;
        }
        return null;
    }

    /**
     * Checks {@code changes} (key -> submitted text) against the rules. Accepted, normalized values
     * go into {@code out}; an unchanged secret (empty submission) is skipped.
     *
     * @param current current values, for rules that span two fields
     * @return an error message for the viewer, or null when everything is valid
     */
    static String validate(Map<String, String> changes, Map<String, String> current, Map<String, String> out) {
        Map<String, String> merged = new HashMap<>(current);
        for (Map.Entry<String, String> e : changes.entrySet()) {
            Field f = find(e.getKey());
            if (f == null) return "不明な設定項目です: " + e.getKey();
            String v = e.getValue() == null ? "" : e.getValue().trim();
            switch (f.type) {
                case LIST: {
                    boolean ok = false;
                    for (String a : f.values) ok |= a.equals(v);
                    if (!ok) return "選択できない値です: " + f.key;
                    break;
                }
                case INT: {
                    int n;
                    try {
                        n = Integer.parseInt(v);
                    } catch (NumberFormatException ex) {
                        return "数字を入力してください: " + f.key;
                    }
                    if (n < f.min || n > f.max) return f.key + " は " + f.min + "〜" + f.max + " の範囲で入力してください";
                    v = String.valueOf(n);
                    break;
                }
                case BOOL:
                    if (!v.equals("true") && !v.equals("false")) return "オン/オフの値が不正です: " + f.key;
                    break;
                case SECRET:
                    if (v.isEmpty()) continue; // empty = keep the current secret
                    if (v.equals("-")) {        // explicit clear
                        v = "";
                        break;
                    }
                    if (f.key.equals("password") && v.length() < RemoteAccess.MIN_PASSWORD_LENGTH) {
                        return "パスワードは" + RemoteAccess.MIN_PASSWORD_LENGTH + "文字以上にしてください";
                    }
                    if (v.length() > 128 || v.indexOf('\n') >= 0) return "長すぎるか、使えない文字があります: " + f.key;
                    break;
                case TEXT:
                    if (f.key.equals("external_host") && !v.isEmpty() && !v.matches("[A-Za-z0-9.:-]{1,253}")) {
                        return "外部アドレスはIPアドレスかドメイン名で入力してください";
                    }
                    if (f.key.equals("quiet_start") || f.key.equals("quiet_end")) {
                        int m = QuietHours.parseHm(v, -1);
                        if (m < 0) return "時刻は 03:00 のように「時:分」で入力してください（0:00〜23:59）";
                        v = QuietHours.format(m);
                    }
                    if (f.key.equals("update_url") && !v.isEmpty()
                            && (!UpdateHttp.isHttpsUrl(v) || v.length() > 512 || v.matches(".*\\s.*"))) {
                        return "更新用URLは https:// で始まるアドレスにしてください（http:// は使えません）";
                    }
                    if (f.key.equals("update_sha256")) {
                        String h = v.toLowerCase(java.util.Locale.US).replace(":", "").replace(" ", "");
                        if (!h.isEmpty() && !h.matches("[0-9a-f]{64}")) return "SHA-256 は 64 桁の16進数で入力してください";
                        v = h;
                    }
                    if (f.key.equals("ddns_domain")) {
                        String d = v.toLowerCase(java.util.Locale.US);
                        if (d.endsWith(".duckdns.org")) d = d.substring(0, d.length() - ".duckdns.org".length());
                        if (!d.isEmpty() && !d.matches("[a-z0-9-]{1,63}")) return "DuckDNS のドメイン名が不正です";
                        v = d;
                    }
                    break;
                default:
                    break;
            }
            out.put(f.key, v);
            merged.put(f.key, v);
        }
        int pause = parseIntOr(merged.get("low_batt_pause"), 18);
        int resume = parseIntOr(merged.get("low_batt_resume"), 50);
        if (pause > 0 && resume < pause + 5) {
            return "電池の再開レベル（" + resume + "%）は、停止レベル（" + pause + "%）より 5% 以上高くしてください";
        }
        String port = merged.get("port");
        if (port != null && port.equals(merged.get("remote_port"))) {
            return "LAN用ポートと外出先用ポートは別の番号にしてください";
        }
        return null;
    }

    private static int parseIntOr(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (RuntimeException e) {
            return def;
        }
    }
}
