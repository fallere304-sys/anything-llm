package com.z4motioncam;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.Resources;
import android.preference.PreferenceManager;

import java.util.HashMap;
import java.util.Map;

/**
 * Settings over HTTP: the same items as the phone's settings screen, with the same labels and
 * choices (taken from the app resources), stored in the same SharedPreferences.
 */
final class SettingsApi {
    private final Context context;

    SettingsApi(Context context) {
        this.context = context.getApplicationContext();
    }

    private SharedPreferences prefs() {
        return PreferenceManager.getDefaultSharedPreferences(context);
    }

    private String defaultOf(SettingsSpec.Field f) {
        return f.key.equals("external_host") ? context.getString(R.string.default_external_host) : f.def;
    }

    /** Current value as text (booleans as "true"/"false"). */
    private String current(SharedPreferences p, SettingsSpec.Field f) {
        if (f.type == SettingsSpec.Type.BOOL) return String.valueOf(p.getBoolean(f.key, Boolean.parseBoolean(f.def)));
        return p.getString(f.key, defaultOf(f));
    }

    /** Groups and fields for the web form. Secrets are never sent, only whether one is set. */
    String json() {
        Resources r = context.getResources();
        SharedPreferences p = prefs();
        Object[][] groups = {
                {R.string.pref_cat_camera, new String[] {"resolution", "fps", "rotation"}},
                {R.string.pref_cat_motion, new String[] {"sensitivity", "post_record_sec", "segment_min"}},
                {R.string.pref_cat_storage, new String[] {"use_sd", "min_free_mb"}},
                {R.string.pref_cat_network, new String[] {"port"}},
                {R.string.pref_cat_remote, new String[] {"remote_enabled", "password", "external_host", "remote_port",
                        "upnp", "ddns_domain", "ddns_token", "acme"}},
                {R.string.pref_cat_system, new String[] {"autostart"}},
        };
        StringBuilder sb = new StringBuilder("{\"groups\":[");
        for (int g = 0; g < groups.length; g++) {
            if (g > 0) sb.append(',');
            sb.append("{\"title\":").append(HttpServer.jsonString(r.getString((Integer) groups[g][0]))).append(",\"fields\":[");
            String[] keys = (String[]) groups[g][1];
            for (int i = 0; i < keys.length; i++) {
                SettingsSpec.Field f = SettingsSpec.find(keys[i]);
                if (i > 0) sb.append(',');
                sb.append("{\"key\":\"").append(f.key).append("\",\"type\":\"").append(f.type)
                        .append("\",\"label\":").append(HttpServer.jsonString(r.getString(labelOf(f.key))))
                        .append(",\"conn\":").append(f.connection);
                String value = current(p, f);
                if (f.type == SettingsSpec.Type.SECRET) {
                    sb.append(",\"set\":").append(!value.isEmpty());
                } else {
                    sb.append(",\"value\":").append(HttpServer.jsonString(value));
                }
                if (f.type == SettingsSpec.Type.INT) sb.append(",\"min\":").append(f.min).append(",\"max\":").append(f.max);
                if (f.type == SettingsSpec.Type.LIST) {
                    String[] labels = r.getStringArray(entriesOf(f.key));
                    sb.append(",\"options\":[");
                    for (int k = 0; k < f.values.length; k++) {
                        if (k > 0) sb.append(',');
                        sb.append("{\"v\":").append(HttpServer.jsonString(f.values[k])).append(",\"l\":")
                                .append(HttpServer.jsonString(k < labels.length ? labels[k] : f.values[k])).append('}');
                    }
                    sb.append(']');
                }
                sb.append('}');
            }
            sb.append("]}");
        }
        return sb.append("]}").toString();
    }

    /** Validates and stores the changes; returns an error message or null. */
    String update(Map<String, String> changes) {
        SharedPreferences p = prefs();
        Map<String, String> current = new HashMap<>();
        for (SettingsSpec.Field f : SettingsSpec.FIELDS) current.put(f.key, current(p, f));
        Map<String, String> accepted = new HashMap<>();
        String error = SettingsSpec.validate(changes, current, accepted);
        if (error != null) return error;
        SharedPreferences.Editor e = p.edit();
        for (Map.Entry<String, String> a : accepted.entrySet()) {
            if (SettingsSpec.find(a.getKey()).type == SettingsSpec.Type.BOOL) e.putBoolean(a.getKey(), Boolean.parseBoolean(a.getValue()));
            else e.putString(a.getKey(), a.getValue());
        }
        return e.commit() ? null : "設定を保存できませんでした";
    }

    private static int labelOf(String key) {
        switch (key) {
            case "resolution": return R.string.pref_resolution;
            case "fps": return R.string.pref_fps;
            case "rotation": return R.string.pref_rotation;
            case "sensitivity": return R.string.pref_sensitivity;
            case "post_record_sec": return R.string.pref_post_record;
            case "segment_min": return R.string.pref_segment;
            case "use_sd": return R.string.pref_use_sd;
            case "min_free_mb": return R.string.pref_min_free;
            case "port": return R.string.pref_port;
            case "remote_enabled": return R.string.pref_remote;
            case "password": return R.string.pref_password;
            case "external_host": return R.string.pref_external_host;
            case "remote_port": return R.string.pref_remote_port;
            case "upnp": return R.string.pref_upnp;
            case "ddns_domain": return R.string.pref_ddns_domain;
            case "ddns_token": return R.string.pref_ddns_token;
            case "acme": return R.string.pref_acme;
            default: return R.string.pref_autostart;
        }
    }

    private static int entriesOf(String key) {
        switch (key) {
            case "resolution": return R.array.resolution_entries;
            case "fps": return R.array.fps_entries;
            case "rotation": return R.array.rotation_entries;
            case "sensitivity": return R.array.sensitivity_entries;
            case "post_record_sec": return R.array.post_record_entries;
            default: return R.array.segment_entries;
        }
    }
}
