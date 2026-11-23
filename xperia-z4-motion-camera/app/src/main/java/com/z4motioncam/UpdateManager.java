package com.z4motioncam;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.preference.PreferenceManager;
import android.provider.Settings;
import android.util.Log;

import java.io.File;
import java.io.InputStream;
import java.security.SecureRandom;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;

import javax.net.ssl.SSLSocketFactory;

/**
 * Owns the update state on the phone: the daily check, the download, an APK pushed from a browser on
 * the home network, and the hand-over to the system installer. Both entrances (the phone fetches
 * from the Internet / the browser sends it, with a code shown only on the phone) end in the same
 * verified file in the same folder and share the installer step. All network and file work runs on one background thread; the camera is never
 * touched. Nothing is installed without the person confirming on Android's own installer screen.
 */
final class UpdateManager {
    enum Phase { IDLE, CHECKING, UP_TO_DATE, AVAILABLE, DOWNLOADING, RECEIVING, READY, ERROR }

    /** What the service provides. */
    interface Host {
        void event(String text);

        /** Show the installer. Called on a background thread; the host moves to the main thread. */
        void launchInstaller(Intent intent);

        /** The phone is warm: skip automatic work. */
        boolean tooWarm();
    }

    private static final String TAG = "UpdateManager";
    static final String PREF_LAST_OK = "update_last_ok";
    static final String PREF_LAST_TRY = "update_last_try";
    static final String PREF_FAILS = "update_fails";
    static final String PREF_MANIFEST = "update_manifest";
    static final String PREF_SEEN_VC = "update_seen_vc";
    static final String PREF_RESTART = "update_restart";
    private static final long MANUAL_GAP_MS = 5_000L;

    private final Context context;
    private final Host host;
    private final SSLSocketFactory tls;
    private final UpdateStore store;
    private final UpdateEngine engine;
    private final UpdatePush push;
    private final ExecutorService executor;
    private final long startedMs = System.currentTimeMillis();

    private volatile Phase phase = Phase.IDLE;
    private volatile UpdateManifest available;
    private volatile String error;
    private volatile long done;
    private volatile long total;
    private volatile boolean cancel;
    private long lastManualMs;

    UpdateManager(Context ctx, Host host, SSLSocketFactory tls) {
        this.context = ctx.getApplicationContext();
        this.host = host;
        this.tls = tls;
        this.store = new UpdateStore(updatesDir(context));
        this.engine = new UpdateEngine(store, new DeviceEnv(context), new UpdateHttp(tls, false));
        this.push = new UpdatePush(engine, new UpdateWindow(new SecureRandom()), pushGate);
        this.executor = Executors.newSingleThreadExecutor(new ThreadFactory() {
            @Override
            public Thread newThread(Runnable r) {
                Thread t = new Thread(r, "update");
                t.setDaemon(true);
                return t;
            }
        });
    }

    /** External app folder (readable by the installer on Android 5-6), else internal storage. */
    static File updatesDir(Context c) {
        File ext = c.getExternalFilesDir("updates");
        if (ext != null && Environment.MEDIA_MOUNTED.equals(Environment.getExternalStorageState(ext))) return ext;
        return new File(c.getFilesDir(), "updates");
    }

    private SharedPreferences prefs() {
        return PreferenceManager.getDefaultSharedPreferences(context);
    }

    private int installedVc() {
        return new DeviceEnv(context).installedVersionCode();
    }

    /** Cleans the folder, reports a completed update, and restores what was found earlier. */
    void start() {
        int cur = installedVc();
        SharedPreferences p = prefs();
        int seen = p.getInt(PREF_SEEN_VC, 0);
        if (seen != 0 && cur > seen) {
            host.event("アプリを更新しました（版 " + seen + " → " + cur + "）。録画と設定は引き継がれています");
        }
        p.edit().putInt(PREF_SEEN_VC, cur).putBoolean(PREF_RESTART, false).apply();
        UpdateManifest m = newer(loadManifest(p, cur), pushedReady(cur));
        available = m;
        store.cleanup(cur, m == null ? -1 : m.versionCode, System.currentTimeMillis());
        if (m != null) phase = isReady(m) ? Phase.READY : Phase.AVAILABLE;
        else phase = Phase.IDLE;
    }

    void shutdown() {
        cancel = true;
        executor.shutdownNow();
    }

    private UpdateManifest loadManifest(SharedPreferences p, int cur) {
        String s = p.getString(PREF_MANIFEST, "");
        if (s.isEmpty()) return null;
        try {
            UpdateManifest m = UpdateManifest.parse(s.getBytes("UTF-8"), new org.json.JSONObject(s).optString("apk"));
            return m.versionCode > cur ? m : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** An APK that was pushed from a browser and is still waiting, rebuilt from the saved meta file. */
    private UpdateManifest pushedReady(int cur) {
        int vc = store.newestReady(cur);
        UpdateStore.Meta meta = vc < 0 ? null : store.readMeta(vc);
        if (meta == null || !UpdateManifest.PUSH_URL.equals(meta.url)) return null;
        return UpdateManifest.pushed(vc, meta.name, meta.sha256, meta.size);
    }

    /** The one to offer: the higher version; the pushed one when both are the same version. */
    private static UpdateManifest newer(UpdateManifest fetched, UpdateManifest pushed) {
        if (pushed != null && (fetched == null || pushed.versionCode >= fetched.versionCode)) return pushed;
        return fetched;
    }

    private boolean isReady(UpdateManifest m) {
        UpdateStore.Meta meta = store.readMeta(m.versionCode);
        return meta != null && meta.matches(m) && store.ready(m.versionCode).isFile();
    }

    String updateUrl() {
        String u = prefs().getString("update_url", "").trim();
        return u.isEmpty() ? context.getString(R.string.default_update_url) : u;
    }

    boolean autoEnabled() {
        return prefs().getBoolean("update_auto", true);
    }

    private boolean online() {
        try {
            ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkInfo ni = cm == null ? null : cm.getActiveNetworkInfo();
            return ni != null && ni.isConnected();
        } catch (RuntimeException e) {
            return true; // unknown: just try
        }
    }

    /** Called about every 15 s: starts the daily check when it is due. */
    void tick() {
        if (!autoEnabled() || busy()) return;
        long now = System.currentTimeMillis();
        if (now - startedMs < UpdateSchedule.STARTUP_DELAY_MS && now >= startedMs) return;
        SharedPreferences p = prefs();
        if (!UpdateSchedule.due(now, p.getLong(PREF_LAST_OK, 0), p.getLong(PREF_LAST_TRY, 0), p.getInt(PREF_FAILS, 0))) return;
        if (host.tooWarm() || !online()) return;
        check(false);
    }

    /** @return null when started, otherwise a message for the person */
    synchronized String check(final boolean manual) {
        if (busy()) return "処理中です。しばらくお待ちください";
        long now = System.currentTimeMillis();
        if (manual && Math.abs(now - lastManualMs) < MANUAL_GAP_MS) return "少し待ってからもう一度押してください";
        if (!online()) {
            if (manual) setError("ネットワークに接続されていません");
            return "ネットワークに接続されていません";
        }
        if (manual) lastManualMs = now;
        phase = Phase.CHECKING;
        error = null;
        executor.execute(new Runnable() {
            @Override
            public void run() {
                runCheck(manual);
            }
        });
        return null;
    }

    private boolean busy() {
        return phase == Phase.CHECKING || phase == Phase.DOWNLOADING || phase == Phase.RECEIVING;
    }

    private void runCheck(boolean manual) {
        SharedPreferences p = prefs();
        long now = System.currentTimeMillis();
        String url = updateUrl();
        try {
            if (!UpdateHttp.isHttpsUrl(url)) {
                throw new UpdateException(UpdateException.Kind.NOT_HTTPS, "更新用URLは https:// で始まるアドレスにしてください");
            }
            UpdateManifest m = engine.check(url, p.getString("update_sha256", ""));
            p.edit().putLong(PREF_LAST_OK, now).putLong(PREF_LAST_TRY, now).putInt(PREF_FAILS, 0).apply();
            int cur = installedVc();
            UpdateManifest pushed = pushedReady(cur);
            if (pushed != null && (m == null || pushed.versionCode >= m.versionCode)) {
                // An APK sent from a browser is already waiting and is not older: keep offering that one.
                available = pushed;
                store.cleanup(cur, pushed.versionCode, now);
                phase = Phase.READY;
            } else if (m == null) {
                available = null;
                p.edit().remove(PREF_MANIFEST).apply();
                store.cleanup(cur, -1, now);
                phase = Phase.UP_TO_DATE;
            } else {
                UpdateManifest before = available;
                available = m;
                p.edit().putString(PREF_MANIFEST, m.toJson().toString()).apply();
                store.cleanup(cur, m.versionCode, now);
                phase = isReady(m) ? Phase.READY : Phase.AVAILABLE;
                if (before == null || before.versionCode != m.versionCode) {
                    host.event("新しい版 " + m.versionName + "（" + m.versionCode + "）があります。更新画面から導入できます");
                }
            }
        } catch (UpdateException e) {
            int fails = p.getInt(PREF_FAILS, 0) + 1;
            p.edit().putLong(PREF_LAST_TRY, now).putInt(PREF_FAILS, fails).apply();
            Log.w(TAG, "check failed: " + e.getMessage());
            if (fails == 1 || manual) host.event("更新の確認に失敗しました: " + e.userMessage);
            setError(e.userMessage);
        } catch (RuntimeException e) {
            Log.e(TAG, "check crashed", e);
            setError("更新の確認中に問題が起きました（" + e.getClass().getSimpleName() + "）");
        }
    }

    private void setError(String msg) {
        error = msg;
        phase = Phase.ERROR;
    }

    /** @return null when started, otherwise a message */
    synchronized String download() {
        final UpdateManifest m = available;
        if (m == null) return "先に「更新を確認」を押してください";
        if (busy()) return "処理中です。しばらくお待ちください";
        if (phase == Phase.READY) return null;
        if (!online()) return "ネットワークに接続されていません";
        phase = Phase.DOWNLOADING;
        error = null;
        done = 0;
        total = m.size;
        cancel = false;
        executor.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    engine.download(m, new UpdateHttp.Progress() {
                        @Override
                        public void onBytes(long d, long t) {
                            done = d;
                            total = t;
                        }

                        @Override
                        public boolean cancelled() {
                            return cancel;
                        }
                    });
                    phase = Phase.READY;
                    host.event("更新 " + m.versionName + " をダウンロードし、確認しました（SHA-256・署名とも一致）");
                } catch (UpdateException e) {
                    Log.w(TAG, "download failed: " + e.getMessage());
                    host.event("更新のダウンロードに失敗しました: " + e.userMessage);
                    setError(e.userMessage);
                } catch (RuntimeException e) {
                    Log.e(TAG, "download crashed", e);
                    setError("ダウンロード中に問題が起きました（" + e.getClass().getSimpleName() + "）");
                }
            }
        });
        return null;
    }

    /**
     * Re-verifies the downloaded file and opens the system installer. Returns at once; problems are
     * reported through the state. @return null when started, otherwise a message
     */
    synchronized String install() {
        final UpdateManifest m = available;
        if (m == null || phase != Phase.READY) return "導入できる更新がありません。先にダウンロードしてください";
        String guide = unknownSourcesAdvice();
        if (guide != null) {
            try {
                // The API 26 constant is spelled out: the app is built against the API 23 android.jar.
                Intent settings = Build.VERSION.SDK_INT >= 26
                        ? new Intent("android.settings.MANAGE_UNKNOWN_APP_SOURCES", Uri.parse("package:" + context.getPackageName()))
                        : new Intent(Settings.ACTION_SECURITY_SETTINGS);
                context.startActivity(settings.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            } catch (RuntimeException ignored) {
                // no such screen: the text explains the path
            }
            return guide;
        }
        executor.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    File apk = engine.verifyReady(m.versionCode);
                    prefs().edit().putBoolean(PREF_RESTART, true).apply();
                    host.event("更新 " + m.versionName + " の導入画面を開きます（Android の標準画面で確認してください）");
                    host.launchInstaller(installIntent(apk));
                } catch (UpdateException e) {
                    host.event("更新を導入できません: " + e.userMessage);
                    setError(e.userMessage);
                }
            }
        });
        return null;
    }

    String unknownSourcesAdvice() {
        boolean allowed = true;
        boolean can = true;
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                // API 26 method, called by reflection (built against the API 23 android.jar).
                PackageManager pm = context.getPackageManager();
                can = Boolean.TRUE.equals(PackageManager.class.getMethod("canRequestPackageInstalls").invoke(pm));
            } else {
                allowed = Settings.Global.getInt(context.getContentResolver(), Settings.Global.INSTALL_NON_MARKET_APPS, 0) == 1
                        || Settings.Secure.getInt(context.getContentResolver(), Settings.Secure.INSTALL_NON_MARKET_APPS, 0) == 1;
            }
        } catch (Exception e) {
            return null; // cannot tell: let the system installer explain
        }
        return InstallGuide.advice(Build.VERSION.SDK_INT, allowed, can);
    }

    private Intent installIntent(File apk) {
        Intent i = new Intent(Intent.ACTION_VIEW).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Uri uri;
        if (Build.VERSION.SDK_INT >= 24) {
            // Android 7+ forbids file:// across apps; a read-only content URI for this one file is used.
            uri = UpdateFileProvider.uriFor(apk);
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } else {
            //noinspection ResultOfMethodCallIgnored
            apk.setReadable(true, false);
            uri = Uri.fromFile(apk);
        }
        return i.setDataAndType(uri, "application/vnd.android.package-archive");
    }

    // ---- the browser entrance ----

    private Phase phaseBeforePush = Phase.IDLE;

    private final UpdatePush.Gate pushGate = new UpdatePush.Gate() {
        @Override
        public String enter() {
            synchronized (UpdateManager.this) {
                if (busy()) return "いま別の更新作業中です。終わってからやり直してください";
                phaseBeforePush = phase;
                phase = Phase.RECEIVING;
                error = null;
                return null;
            }
        }

        @Override
        public void leave(UpdateManifest received) {
            synchronized (UpdateManager.this) {
                if (received != null) {
                    available = received;
                    phase = Phase.READY;
                    host.event("ブラウザから更新 " + received.versionName + " を受信し、確認しました（SHA-256 "
                            + received.sha256.substring(0, 12) + "…）。更新ボタンから導入できます");
                } else {
                    phase = phaseBeforePush == Phase.RECEIVING ? Phase.IDLE : phaseBeforePush;
                }
            }
        }
    };

    /** Opens the window (a new code) unless it is open already; the code is for the phone's screen only. */
    synchronized String openPushWindow() {
        long now = System.currentTimeMillis();
        UpdateWindow w = push.window();
        if (!w.isOpen(now)) {
            w.open(now);
            host.event("ブラウザからの更新の受け付けを始めました（" + UpdateWindow.OPEN_MS / 60_000 + "分間・コードは Z4 の画面だけに表示）");
        }
        return w.code(now);
    }

    /** The code now valid, or null when the window is closed. Never leaves the phone's own screen. */
    String pushCode() {
        return push.window().code(System.currentTimeMillis());
    }

    long pushRemainingMs() {
        return push.window().remainingMs(System.currentTimeMillis());
    }

    /** An APK sent from a browser; called on the HTTP thread. */
    UpdatePush.Result acceptPush(String code, InputStream body, long length) {
        return push.receive(code, body, length, System.currentTimeMillis());
    }

    Phase phase() {
        return phase;
    }

    /** The text of the phone's update dialog: where things stand, and what each entrance does. */
    String dialogText() {
        UpdateManifest m = available;
        StringBuilder sb = new StringBuilder("いまの版: ").append(installedName()).append('（').append(installedVc()).append('）');
        String line = statusLine();
        sb.append('\n').append(line == null ? "更新: 新しい版はまだ見つかっていません" : line);
        String guide = phase == Phase.READY ? unknownSourcesAdvice() : null;
        if (guide != null) sb.append("\n\n※ ").append(guide);
        if (phase == Phase.READY && m != null) {
            sb.append("\nSHA-256 ").append(m.sha256.substring(0, 16)).append("…（")
                    .append(m.isPushed() ? "ブラウザから受信" : "インターネットから取得").append('）');
        }
        sb.append("\n\n更新の入口は2つあります。どちらも同じ検証（SHA-256・署名・版）を通り、最後は Android の確認画面で決めます。")
                .append("\n・インターネットから: Z4 が更新先（https）から取得します（自動確認は1日1回）")
                .append("\n・ブラウザから送る: 同じ Wi-Fi のパソコンから APK を送ります（USB 不要・コードをこの画面に表示）");
        return sb.toString();
    }

    /** What the single button on the phone does now: check, download or install. */
    String primaryAction() {
        UpdateManifest m = available;
        if (m == null) return "check";
        return phase == Phase.READY ? "install" : "download";
    }

    /** One short line for the phone's status screen, or null when there is nothing to say. */
    String statusLine() {
        UpdateManifest m = available;
        switch (phase) {
            case CHECKING: return "更新: 確認中…";
            case DOWNLOADING:
                return String.format(Locale.US, "更新: ダウンロード中 %d%%", total <= 0 ? 0 : (int) (done * 100 / total));
            case READY: return "更新: " + (m == null ? "" : m.versionName + " を導入できます（「更新」ボタン）");
            case AVAILABLE: return "更新: 新しい版 " + (m == null ? "" : m.versionName) + " があります（「更新」ボタン）";
            case RECEIVING: return "更新: ブラウザから受信中…";
            case ERROR: return "更新: " + error;
            default:
                return push.window().isOpen(System.currentTimeMillis())
                        ? "更新: ブラウザからの受け付け中（あと" + (pushRemainingMs() / 60_000 + 1) + "分）" : null;
        }
    }

    String buttonLabel() {
        switch (phase) {
            case CHECKING: return "確認中…";
            case DOWNLOADING: return total <= 0 ? "取得中…" : "取得 " + (int) (done * 100 / total) + "%";
            case RECEIVING: return "受信中…";
            case READY: return "更新を導入";
            case AVAILABLE: return "更新あり";
            default: return "更新";
        }
    }

    String json() {
        UpdateManifest m = available;
        SharedPreferences p = prefs();
        long now = System.currentTimeMillis();
        long lastOk = p.getLong(PREF_LAST_OK, 0);
        long next = autoEnabled()
                ? UpdateSchedule.nextMs(now, lastOk, p.getLong(PREF_LAST_TRY, 0), p.getInt(PREF_FAILS, 0)) : 0;
        String guide = phase == Phase.READY ? unknownSourcesAdvice() : null;
        StringBuilder sb = new StringBuilder("{\"phase\":\"").append(phase.name().toLowerCase(Locale.US)).append("\"");
        sb.append(",\"installed\":{\"code\":").append(installedVc()).append(",\"name\":")
                .append(HttpServer.jsonString(installedName())).append('}');
        sb.append(",\"latest\":");
        if (m == null) {
            sb.append("null");
        } else {
            sb.append("{\"code\":").append(m.versionCode).append(",\"name\":").append(HttpServer.jsonString(m.versionName))
                    .append(",\"size\":").append(m.size).append(",\"notes\":").append(HttpServer.jsonString(m.notes))
                    .append(",\"sha256\":").append(HttpServer.jsonString(m.sha256))
                    .append(",\"source\":\"").append(m.isPushed() ? "push" : "internet").append("\"}");
        }
        sb.append(",\"progress\":").append(String.format(Locale.US, "%.3f", total <= 0 ? 0f : done / (float) total));
        sb.append(",\"error\":").append(error == null || phase != Phase.ERROR ? "null" : HttpServer.jsonString(error));
        sb.append(",\"lastCheck\":").append(lastOk);
        sb.append(",\"nextCheck\":").append(next);
        sb.append(",\"auto\":").append(autoEnabled());
        sb.append(",\"pinned\":").append(!p.getString("update_sha256", "").isEmpty());
        sb.append(",\"action\":\"").append(primaryAction()).append('"');
        long nowMs = System.currentTimeMillis();
        sb.append(",\"push\":{\"open\":").append(push.window().isOpen(nowMs))
                .append(",\"remainingSec\":").append(pushRemainingMs() / 1000)
                .append(",\"maxMb\":").append(UpdateEngine.PUSH_MAX_BYTES / 1024 / 1024).append('}');
        sb.append(",\"guide\":").append(guide == null ? "null" : HttpServer.jsonString(guide));
        return sb.append('}').toString();
    }

    private String installedName() {
        try {
            return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException | RuntimeException e) {
            return "?";
        }
    }

    /** The device side of {@link UpdateEngine.Env}. */
    @SuppressWarnings("deprecation") // GET_SIGNATURES is the call that works on Android 5-7
    static final class DeviceEnv implements UpdateEngine.Env {
        private final Context context;

        DeviceEnv(Context c) {
            this.context = c;
        }

        @Override
        public String packageName() {
            return context.getPackageName();
        }

        @Override
        public int installedVersionCode() {
            try {
                return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionCode;
            } catch (PackageManager.NameNotFoundException e) {
                return 0;
            }
        }

        @Override
        public long wallMs() {
            return System.currentTimeMillis();
        }

        @Override
        public Set<String> installedSigners() {
            try {
                PackageInfo pi = context.getPackageManager().getPackageInfo(context.getPackageName(),
                        PackageManager.GET_SIGNATURES);
                return hexes(pi.signatures);
            } catch (PackageManager.NameNotFoundException e) {
                return new HashSet<>();
            }
        }

        @Override
        public UpdateEngine.ApkInfo readApk(File apk) {
            PackageInfo pi = context.getPackageManager().getPackageArchiveInfo(apk.getPath(), PackageManager.GET_SIGNATURES);
            if (pi == null) return null;
            return new UpdateEngine.ApkInfo(pi.packageName, pi.versionCode, hexes(pi.signatures), pi.versionName);
        }

        private static Set<String> hexes(Signature[] sigs) {
            Set<String> out = new HashSet<>();
            if (sigs != null) for (Signature s : sigs) out.add(ApkCheck.sha256Hex(s.toByteArray()));
            return out;
        }
    }
}
