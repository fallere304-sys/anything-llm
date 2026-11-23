package com.z4motioncam;

import org.json.JSONException;
import org.json.JSONObject;

import java.net.MalformedURLException;
import java.net.URL;
import java.nio.charset.Charset;
import java.util.Locale;

/**
 * The small JSON published next to the APK:
 * <pre>{"package":"com.z4motioncam","versionCode":12,"versionName":"1.12",
 *  "apk":"z4motioncam-1.12.apk","sha256":"&lt;64 hex&gt;","size":123456,"notes":"..."}</pre>
 * "apk" may be relative to the JSON's own URL. Everything is validated; nothing here is trusted
 * beyond "this is what the publisher says": the APK is still checked against its SHA-256 and, the
 * part that matters most, against the installed app's signing certificate.
 */
final class UpdateManifest {
    static final String PACKAGE = "com.z4motioncam";
    static final int MAX_JSON_BYTES = 16 * 1024;
    static final long MIN_APK_BYTES = 20_000L;
    static final long MAX_APK_BYTES = 64L * 1024 * 1024;

    final String pkg;
    final int versionCode;
    final String versionName;
    final String apkUrl;
    final String sha256;
    final long size;
    final String notes;

    private UpdateManifest(String pkg, int versionCode, String versionName, String apkUrl, String sha256, long size,
                           String notes) {
        this.pkg = pkg;
        this.versionCode = versionCode;
        this.versionName = versionName;
        this.apkUrl = apkUrl;
        this.sha256 = sha256;
        this.size = size;
        this.notes = notes;
    }

    static UpdateManifest parse(byte[] json, String manifestUrl) throws UpdateException {
        if (json == null || json.length == 0 || json.length > MAX_JSON_BYTES) throw bad("更新情報の大きさが正しくありません");
        JSONObject o;
        try {
            o = new JSONObject(new String(json, Charset.forName("UTF-8")));
        } catch (JSONException e) {
            throw bad("更新情報が JSON として読めません");
        }
        String pkg = o.optString("package", PACKAGE);
        if (!PACKAGE.equals(pkg)) throw bad("別のアプリ用の更新情報です（" + printable(pkg, 60) + "）");
        int code = o.optInt("versionCode", -1);
        if (code < 1) throw bad("versionCode がありません");
        String name = printable(o.optString("versionName", String.valueOf(code)), 32);
        String sha = o.optString("sha256", "").trim().toLowerCase(Locale.US);
        if (!sha.matches("[0-9a-f]{64}")) throw bad("sha256 は 64 桁の16進数で指定してください");
        long size = o.optLong("size", -1);
        if (size < MIN_APK_BYTES || size > MAX_APK_BYTES) throw bad("size が範囲外です（" + MIN_APK_BYTES + "〜" + MAX_APK_BYTES + " バイト）");
        String apk = o.optString("apk", "").trim();
        if (apk.isEmpty() || apk.length() > 600) throw bad("apk のアドレスがありません");
        String resolved;
        try {
            resolved = new URL(new URL(manifestUrl), apk).toString();
        } catch (MalformedURLException e) {
            throw bad("apk のアドレスが正しくありません");
        }
        if (!UpdateHttp.isHttpsUrl(resolved) && !manifestUrl.toLowerCase(Locale.US).startsWith("http://")) {
            // http:// is only ever reachable in unit tests (UpdateHttp refuses it otherwise); a
            // manifest fetched over https must not point to plain http.
            throw new UpdateException(UpdateException.Kind.NOT_HTTPS, "APK の取得先が https:// ではありません");
        }
        return new UpdateManifest(pkg, code, name, resolved, sha, size, printable(o.optString("notes", ""), 400));
    }

    JSONObject toJson() {
        try {
            JSONObject o = new JSONObject();
            o.put("package", pkg);
            o.put("versionCode", versionCode);
            o.put("versionName", versionName);
            o.put("apk", apkUrl);
            o.put("sha256", sha256);
            o.put("size", size);
            o.put("notes", notes);
            return o;
        } catch (JSONException e) {
            throw new IllegalStateException(e);
        }
    }

    private static UpdateException bad(String msg) {
        return new UpdateException(UpdateException.Kind.BAD_MANIFEST, "更新情報の形式が正しくありません: " + msg);
    }

    /** Strips control characters and truncates: the text ends up on the status screen and web page. */
    static String printable(String s, int max) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length() && sb.length() < max; i++) {
            char c = s.charAt(i);
            sb.append(c < 0x20 || c == 0x7f ? ' ' : c);
        }
        return sb.toString().trim();
    }
}
