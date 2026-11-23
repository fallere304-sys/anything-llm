package com.z4motioncam;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.Binder;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.preference.PreferenceManager;
import android.provider.Settings;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

import javax.net.ssl.SSLSocketFactory;

/**
 * Foreground service that owns the camera pipeline and the web server, so monitoring continues
 * even when the activity is not in front. Also watches the battery temperature and throttles or
 * pauses the camera before the phone overheats.
 */
public class CameraService extends Service implements HttpServer.Backend {
    static final String ACTION_RELOAD = "com.z4motioncam.RELOAD";
    private static final String TAG = "CameraService";
    private static final int NOTIFICATION_ID = 1;

    final class LocalBinder extends Binder {
        CameraService service() {
            return CameraService.this;
        }
    }

    private final IBinder binder = new LocalBinder();
    private final FrameHub hub = new FrameHub();
    private volatile AppSettings settings;
    private volatile RecordingStore store;
    private volatile CameraPipeline pipeline;
    private volatile RemoteAccess remote;
    private SSLSocketFactory ddnsTls;
    private HttpServer http;
    private String httpError;
    private byte[] indexHtml;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private PowerManager power;
    /** Turns the screen on (dim) for a moment when the app is brought back after the screen went off. */
    private PowerManager.WakeLock screenOn;
    private final KeepFront keepFront = new KeepFront();
    private boolean wasVisible = true;
    private static final long FRONT_CHECK_MS = 5_000L;

    private volatile ThermalPolicy.Level thermal = ThermalPolicy.Level.NORMAL;
    private volatile float batteryTempC = Float.NaN;
    private volatile int batteryPct = -1;
    private volatile boolean charging;
    private volatile int batteryVoltageMv = -1;
    private volatile String powerState = "";
    private volatile String batteryHealth = "";
    private volatile UptimeLog uptime;
    /**
     * Monitoring on/off, switchable from the phone or a browser and kept across restarts. Off stops
     * only the camera (no detection, recording or live view); the web servers, locks and the dark
     * screen stay, so it can be switched back on at any time.
     */
    private volatile boolean monitoring = true;
    static final String PREF_MONITORING = "monitoring";
    private SettingsApi settingsApi;
    /** Decides whether the camera may run (user switch, heat, battery, sleep time, update); see MonitorPolicy. */
    private MonitorPolicy policy;
    private volatile MonitorPolicy.Decision decision;
    private String savedPolicyState = "";
    static final String PREF_POLICY = "policy_state";
    /** While the installer screen is up the camera is stopped (a recording in progress is closed properly). */
    private long updatingUntilMs;
    private static final long UPDATE_HOLD_MS = 3 * 60_000L;
    private static final long POLICY_TICK_MS = 15_000L;
    private final ClockWatch clockWatch = new ClockWatch();
    private UpdateManager updates;
    private final Handler main = new Handler(Looper.getMainLooper());

    private final CameraPipeline.Events pipelineEvents = new CameraPipeline.Events() {
        @Override
        public void event(String text) {
            UptimeLog u = uptime;
            if (u != null) u.event(text);
        }
    };

    /**
     * Watches whether the app's screen is showing. Monitoring itself runs in this service, but some
     * phones and tablets stop the camera or the app once it is hidden or the screen sleeps, so the
     * app is brought back to the front with the screen on (dark) by default.
     */
    private final Runnable frontCheck = new Runnable() {
        @Override
        public void run() {
            main.postDelayed(this, FRONT_CHECK_MS);
            boolean visible = AppUi.visible();
            boolean interactive = power.isInteractive();
            if (wasVisible && !visible) {
                uptime.event(interactive ? "アプリが背面に回りました（ほかのアプリやホーム画面が前面に）" : "画面が消えました");
            }
            wasVisible = visible;
            if (!settings.keepFront) return;
            switch (keepFront.check(SystemClock.elapsedRealtime(), visible, interactive)) {
                case RELAUNCH:
                    bringToFront(interactive);
                    break;
                case PAUSE:
                    uptime.event("前面に戻してもすぐに閉じられるため、自動で戻すのを" + KeepFront.PAUSE_MS / 60_000
                            + "分休みます（端末の利用時間制限・省電力設定などを確認）");
                    break;
                default:
                    break;
            }
        }
    };

    @SuppressWarnings("deprecation") // SCREEN_DIM_WAKE_LOCK: the only way for a service to wake the screen.
    private void bringToFront(boolean interactive) {
        if (!interactive) screenOn.acquire(10_000L);
        try {
            startActivity(new Intent(this, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra(MainActivity.EXTRA_START_DARK, true));
        } catch (RuntimeException e) {
            Log.w(TAG, "cannot bring the app to the front", e);
        }
        uptime.event((interactive ? "アプリを前面に戻しました" : "画面を点けてアプリを前面に戻しました（画面は暗いまま）")
                + (overlayPermissionMissing() ? "。Android 10 以降は「他のアプリの上に重ねて表示」の許可がないと戻せません" : ""));
    }

    /** Android 10+ only lets a background app open its screen with the "display over other apps" permission. */
    boolean overlayPermissionMissing() {
        return Build.VERSION.SDK_INT >= 29 && !Settings.canDrawOverlays(this);
    }

    /** Re-evaluates the policy regularly: the sleep window opens and closes with the clock, not with an event. */
    private final Runnable policyTick = new Runnable() {
        @Override
        public void run() {
            main.postDelayed(this, POLICY_TICK_MS);
            long jump = clockWatch.observe(System.currentTimeMillis(), SystemClock.elapsedRealtime());
            if (jump != 0) uptime.event(ClockWatch.describe(jump));
            applyPolicy();
            if (updates != null) updates.tick();
        }
    };

    private final BroadcastReceiver clockReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String a = intent.getAction();
            if (Intent.ACTION_TIMEZONE_CHANGED.equals(a)) {
                uptime.event("タイムゾーンが " + TimeZone.getDefault().getID() + " に変わりました");
            } else {
                long jump = clockWatch.observe(System.currentTimeMillis(), SystemClock.elapsedRealtime());
                if (jump != 0) uptime.event(ClockWatch.describe(jump));
            }
            applyPolicy();
        }
    };

    private final UpdateManager.Host updateHost = new UpdateManager.Host() {
        @Override
        public void event(String text) {
            UptimeLog u = uptime;
            if (u != null) u.event(text);
        }

        @Override
        public void launchInstaller(final Intent intent) {
            main.post(new Runnable() {
                @Override
                public void run() {
                    updatingUntilMs = SystemClock.elapsedRealtime() + UPDATE_HOLD_MS;
                    applyPolicy(); // stops the camera and closes the recording before the installer takes over
                    if (!power.isInteractive()) screenOn.acquire(10_000L);
                    try {
                        startActivity(intent);
                    } catch (RuntimeException e) {
                        Log.w(TAG, "cannot open the installer", e);
                        uptime.event("導入画面を開けませんでした: " + e.getClass().getSimpleName());
                        updatingUntilMs = 0;
                        applyPolicy();
                    }
                }
            });
        }

        @Override
        public boolean tooWarm() {
            return thermal.ordinal() >= ThermalPolicy.Level.HOT.ordinal();
        }
    };

    private final Runnable heartbeat = new Runnable() {
        @Override
        public void run() {
            uptime.beat(snapshot());
            main.postDelayed(this, UptimeLog.BEAT_MS);
        }
    };

    private final BroadcastReceiver shutdownReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            // The OS announces a normal shutdown / reboot (also its low-battery and overheat ones).
            uptime.end(UptimeLog.END_SHUTDOWN, snapshot());
        }
    };

    private final BroadcastReceiver networkReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            // The global IP may have changed after a reconnect: re-check mapping and DDNS.
            RemoteAccess r = remote;
            if (r != null) r.refreshSoon();
        }
    };

    private final BroadcastReceiver batteryReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            onBattery(intent);
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        startForeground(NOTIFICATION_ID, buildNotification());

        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        power = pm;
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Z4MotionCam:camera");
        wakeLock.acquire();
        //noinspection deprecation
        screenOn = pm.newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP
                | PowerManager.ON_AFTER_RELEASE, "Z4MotionCam:screen");
        screenOn.setReferenceCounted(false);
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        if (wm != null) {
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL, "Z4MotionCam:wifi");
            wifiLock.acquire();
        }

        try {
            InputStream in = getAssets().open("index.html");
            try {
                indexHtml = HttpServer.readAll(in);
            } finally {
                in.close();
            }
        } catch (IOException e) {
            indexHtml = "<h1>index.html missing</h1>".getBytes();
        }

        // The state the decision depends on must exist before the first settings / battery callback.
        settingsApi = new SettingsApi(this);
        SharedPreferences prefs = PreferenceManager.getDefaultSharedPreferences(this);
        monitoring = prefs.getBoolean(PREF_MONITORING, true);
        uptime = new UptimeLog(new File(getFilesDir(), "uptime"), new UptimeLog.Clock() {
            @Override
            public long wallMs() {
                return System.currentTimeMillis();
            }

            @Override
            public long sinceBootMs() {
                return SystemClock.elapsedRealtime();
            }
        });
        policy = new MonitorPolicy(loadPolicyConfig());
        savedPolicyState = prefs.getString(PREF_POLICY, "");
        policy.restore(savedPolicyState); // low-battery pause and sleep-time state survive a restart
        ddnsTls = loadDdnsTls(); // extra roots: Android 5-7 lack some current CAs (DDNS, update server)
        updates = new UpdateManager(this, updateHost, ddnsTls);
        updates.start();

        // Sticky broadcast: returns the current state now, then delivers every change. Registered
        // first so the very first decision already knows the battery level.
        Intent battery = registerReceiver(batteryReceiver, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (battery != null) onBattery(battery);
        applySettings(AppSettings.load(this));
        registerReceiver(networkReceiver, new IntentFilter(ConnectivityManager.CONNECTIVITY_ACTION));
        IntentFilter clock = new IntentFilter(Intent.ACTION_TIME_CHANGED);
        clock.addAction(Intent.ACTION_TIMEZONE_CHANGED);
        clock.addAction(Intent.ACTION_DATE_CHANGED);
        registerReceiver(clockReceiver, clock);
        clockWatch.observe(System.currentTimeMillis(), SystemClock.elapsedRealtime());

        uptime.begin(snapshot());
        registerReceiver(shutdownReceiver, new IntentFilter(Intent.ACTION_SHUTDOWN));
        applyPolicy();
        main.postDelayed(heartbeat, UptimeLog.BEAT_MS);
        main.postDelayed(frontCheck, FRONT_CHECK_MS);
        main.postDelayed(policyTick, POLICY_TICK_MS);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_RELOAD.equals(intent.getAction())) reloadSettings();
        return START_STICKY;
    }

    /** Applies settings changed on the phone or from a browser (no-op when nothing changed). */
    private void reloadSettings() {
        AppSettings s = AppSettings.load(this);
        if (!s.equals(settings)) applySettings(s);
        else applyPolicy(); // sleep time / battery levels live outside AppSettings
    }

    private final Runnable reload = new Runnable() {
        @Override
        public void run() {
            reloadSettings();
        }
    };

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public void onDestroy() {
        main.removeCallbacks(heartbeat);
        main.removeCallbacks(frontCheck);
        main.removeCallbacks(policyTick);
        if (updates != null) updates.shutdown();
        try {
            unregisterReceiver(clockReceiver);
        } catch (IllegalArgumentException ignored) {
            // never registered
        }
        if (screenOn != null && screenOn.isHeld()) screenOn.release();
        uptime.end(UptimeLog.END_STOP, snapshot()); // no-op after a shutdown notice
        unregisterReceiver(shutdownReceiver);
        unregisterReceiver(batteryReceiver);
        unregisterReceiver(networkReceiver);
        stopPipeline();
        if (remote != null) remote.shutdown();
        if (http != null) http.stop();
        hub.close();
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        stopForeground(true);
        super.onDestroy();
    }

    private void applySettings(AppSettings s) {
        AppSettings old = settings;
        settings = s;
        stopPipeline();
        if (remote != null) {
            remote.shutdown();
            remote = null;
        }
        store = new RecordingStore(recordingsDir(s.useSdCard), s.minFreeBytes);
        store.deleteStaleParts();
        store.setThumbMaker(new AndroidThumbMaker());
        store.pruneThumbs();
        if (http == null || old == null || old.port != s.port) {
            if (http != null) http.stop();
            // LAN server: plain HTTP, refuses connections from outside the home network.
            http = new HttpServer(s.port, this, null, true, false);
            try {
                http.start();
                httpError = null;
            } catch (IOException e) {
                Log.e(TAG, "http start failed", e);
                httpError = "ポート " + s.port + " を開けません";
                http = null;
            }
        }
        applyPolicy(); // starts the camera unless something says it must stay off
        if (s.ddnsConfigured() && ddnsTls == null) ddnsTls = loadDdnsTls();
        remote = new RemoteAccess(this, s, this, ddnsTls);
        remote.start();
    }

    private void startPipeline() {
        if (pipeline != null) return;
        pipeline = new CameraPipeline(this, settings, store, hub, pipelineEvents);
        pipeline.setThermalLevel(thermal);
        pipeline.start();
    }

    private void stopPipeline() {
        if (pipeline == null) return;
        pipeline.shutdown();
        pipeline = null;
    }

    private void onBattery(Intent i) {
        int t = i.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Integer.MIN_VALUE);
        int level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        batteryPct = level < 0 || scale <= 0 || level > scale ? -1 : level * 100 / scale; // -1 = could not be read
        charging = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
        batteryVoltageMv = i.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1);
        int status = i.getIntExtra(BatteryManager.EXTRA_STATUS, BatteryManager.BATTERY_STATUS_UNKNOWN);
        if (!charging) powerState = "未接続";
        else if (status == BatteryManager.BATTERY_STATUS_FULL) powerState = "満充電";
        else if (status == BatteryManager.BATTERY_STATUS_CHARGING) powerState = "充電中";
        else powerState = "接続中・充電停止"; // plugged in but the charger stopped (e.g. battery too hot)
        batteryHealth = healthText(i.getIntExtra(BatteryManager.EXTRA_HEALTH, BatteryManager.BATTERY_HEALTH_UNKNOWN));
        if (t != Integer.MIN_VALUE) {
            batteryTempC = t / 10f;
            ThermalPolicy.Level prev = thermal;
            ThermalPolicy.Level next = ThermalPolicy.next(prev, batteryTempC);
            if (next != prev) {
                thermal = next;
                Log.w(TAG, "thermal " + prev + " -> " + next + " (" + batteryTempC + "C)");
                CameraPipeline p = pipeline;
                if (p != null) p.setThermalLevel(next);
            }
        }
        // Heat (CRITICAL: release the camera until it cools), low battery and sleep time are all
        // decided together in applyPolicy.
        applyPolicy();
    }

    private MonitorPolicy.Config loadPolicyConfig() {
        SharedPreferences p = PreferenceManager.getDefaultSharedPreferences(this);
        return MonitorPolicy.Config.parse(p.getString("low_batt_pause", "18"), p.getString("low_batt_resume", "50"),
                p.getBoolean("quiet_enabled", true), p.getString("quiet_start", "03:00"), p.getString("quiet_end", "04:00"),
                p.getString("quiet_keep_pct", "80"));
    }

    /**
     * The one place that starts or stops the camera: asks {@link MonitorPolicy} and acts on its
     * answer, journals every transition and keeps the latches for the next start. Main thread.
     */
    private void applyPolicy() {
        if (policy == null || uptime == null || settings == null) return; // not set up yet
        MonitorPolicy.Input in = new MonitorPolicy.Input();
        in.monoMs = SystemClock.elapsedRealtime();
        in.wallMs = System.currentTimeMillis();
        in.zone = TimeZone.getDefault();
        in.userOn = monitoring;
        in.thermalCritical = thermal == ThermalPolicy.Level.CRITICAL;
        in.updating = in.monoMs < updatingUntilMs;
        in.batteryPct = batteryPct;
        policy.setConfig(loadPolicyConfig());
        MonitorPolicy.Decision d = policy.evaluate(in);
        decision = d;
        for (String e : d.events) uptime.event(e);
        String state = policy.serialize();
        if (!state.equals(savedPolicyState)) {
            savedPolicyState = state;
            PreferenceManager.getDefaultSharedPreferences(this).edit().putString(PREF_POLICY, state).apply();
        }
        if (d.active()) {
            if (pipeline == null) startPipeline();
        } else {
            stopPipeline(); // closes the recording in progress properly
        }
    }

    private static String healthText(int h) {
        switch (h) {
            case BatteryManager.BATTERY_HEALTH_GOOD: return "良好";
            case BatteryManager.BATTERY_HEALTH_OVERHEAT: return "過熱";
            case BatteryManager.BATTERY_HEALTH_DEAD: return "劣化";
            case BatteryManager.BATTERY_HEALTH_OVER_VOLTAGE: return "過電圧";
            case BatteryManager.BATTERY_HEALTH_UNSPECIFIED_FAILURE: return "異常";
            case BatteryManager.BATTERY_HEALTH_COLD: return "低温";
            default: return "不明";
        }
    }

    UptimeLog.Snapshot snapshot() {
        UptimeLog.Snapshot s = new UptimeLog.Snapshot();
        s.batteryPct = batteryPct;
        s.tempC = batteryTempC;
        s.voltageMv = batteryVoltageMv;
        s.power = powerState;
        s.health = batteryHealth;
        s.thermal = thermal.name();
        CameraPipeline p = pipeline;
        s.recording = p != null && p.isRecording();
        return s;
    }

    /** TLS for the DuckDNS update: Android 5-7 lack some current root CAs, so bundled ones are added. */
    private SSLSocketFactory loadDdnsTls() {
        try {
            InputStream in = getAssets().open("cacerts.pem");
            try {
                return NetUtil.clientTls(NetUtil.parsePem(new String(HttpServer.readAll(in), "UTF-8")));
            } finally {
                in.close();
            }
        } catch (Exception e) {
            Log.w(TAG, "extra roots unavailable", e);
            return null;
        }
    }

    /** SD card (Xperia Z4 has a microSD slot) when present and preferred, else internal storage. */
    private File recordingsDir(boolean preferSd) {
        File[] dirs = getExternalFilesDirs(Environment.DIRECTORY_MOVIES);
        File chosen = null;
        if (preferSd) {
            for (int i = 1; i < dirs.length; i++) {
                if (dirs[i] != null && Environment.MEDIA_MOUNTED.equals(Environment.getExternalStorageState(dirs[i]))) {
                    chosen = dirs[i];
                    break;
                }
            }
        }
        if (chosen == null && dirs.length > 0 && dirs[0] != null) chosen = dirs[0];
        if (chosen == null) chosen = new File(getFilesDir(), "movies");
        return new File(chosen, "recordings");
    }

    private Notification buildNotification() {
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this)
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .setContentTitle(getString(R.string.notif_title))
                .setContentIntent(open)
                .setOngoing(true)
                .setPriority(Notification.PRIORITY_LOW)
                .build();
    }

    // ---- accessors for the activity and the web server ----

    FrameHub frameHub() {
        return hub;
    }

    AppSettings settings() {
        return settings;
    }

    String url() {
        String ip = NetUtil.localIpv4();
        return "http://" + (ip == null ? "(未接続)" : ip) + ":" + settings.port + "/";
    }

    /** One-screen status for the activity. */
    String statusText() {
        CameraPipeline p = pipeline;
        StringBuilder sb = new StringBuilder();
        sb.append(url()).append('\n');
        if (httpError != null) sb.append(httpError).append('\n');
        MonitorPolicy.Decision d = decision;
        if (!monitoring && d != null && d.reasons.size() == 1) {
            sb.append("■ 監視オフ（カメラ停止中。ブラウザまたはこの画面から再開できます）");
        } else if (d != null && !d.active()) {
            sb.append("■ 監視停止中: ").append(d.text);
        } else if (p == null) {
            sb.append("停止中");
        } else if (!p.isRunning()) {
            sb.append("カメラ準備中");
        } else {
            sb.append(p.isRecording() ? "● 録画中" : "監視中");
            sb.append(String.format(Locale.US, "  動き %.1f%%", p.motionRatio() * 100));
            if (settings.personMode()) sb.append(p.personPresent() ? "  人: あり" : "  人: なし");
        }
        sb.append('\n');
        sb.append("検知モード: ").append(settings.personMode()
                ? "人を認識（動きを見て、顔・形・体のモデルで人を判定。厳しさ " + settings.personSens + "）" : "動体検知（動きがあれば録画）");
        if (settings.personMode() && p != null) {
            String bm = bodyModelText(p.bodyModelState());
            if (!bm.isEmpty()) sb.append('\n').append(bm);
        }
        sb.append('\n');
        sb.append(String.format(Locale.US, "電池 %.1f℃ %s %s  負荷制御:%s", batteryTempC,
                batteryPct < 0 ? "" : batteryPct + "%", powerState, thermal));
        sb.append('\n');
        long m = uptime.minutes();
        sb.append(String.format(Locale.US, "稼働 %d時間%02d分（%s から）", m / 60, m % 60,
                new SimpleDateFormat("M/d HH:mm", Locale.US).format(new Date(uptime.startWallMs()))));
        sb.append('\n');
        sb.append(String.format(Locale.US, "空き %.1f GB / 録画 %d 件", store.usableBytes() / 1e9, store.list().size()));
        if (p != null && p.storageFull()) sb.append("\n空き容量不足");
        if (p != null && p.error() != null) sb.append('\n').append(p.error());
        RemoteAccess r = remote;
        String rl = r == null ? null : r.statusLine();
        if (rl != null) sb.append('\n').append(rl);
        String ul = updates == null ? null : updates.statusLine();
        if (ul != null) sb.append('\n').append(ul);
        if (settings.keepFront && overlayPermissionMissing()) {
            sb.append("\n自動で前面に戻すには「他のアプリの上に重ねて表示」を許可してください");
        }
        return sb.toString();
    }

    @Override
    public byte[] indexHtml() {
        return indexHtml;
    }

    @Override
    public String statusJson() {
        CameraPipeline p = pipeline;
        String state;
        if (!monitoring) state = "paused";
        else if (p == null) state = thermal == ThermalPolicy.Level.CRITICAL ? "cooling" : "stopped";
        else if (!p.isRunning()) state = "starting";
        else state = p.isRecording() ? "recording" : "watching";
        String err = p == null ? null : p.error();
        String base = String.format(Locale.US,
                "{\"state\":\"%s\",\"motion\":%.4f,\"lastMotion\":%d,\"tempC\":%.1f,\"battery\":%d,\"power\":%s,"
                        + "\"charging\":%b,\"thermal\":\"%s\",\"freeBytes\":%d,\"storageFull\":%b,"
                        + "\"rotation\":%d,\"viewers\":%d,\"error\":%s,\"monitoring\":%b,\"remote\":%s}",
                state, p == null ? 0f : p.motionRatio(), p == null ? 0L : p.lastMotionWallMs(),
                Float.isNaN(batteryTempC) ? 0f : batteryTempC, batteryPct, HttpServer.jsonString(powerState), charging, thermal,
                store.usableBytes(), p != null && p.storageFull(), settings.rotation, hub.clients(),
                err == null ? "null" : HttpServer.jsonString(err), monitoring,
                remote == null ? "null" : remote.statusJson());
        // New items are appended only: gas-viewer and older pages read the ones above.
        return base.substring(0, base.length() - 1) + extraStatusJson(p) + "}";
    }

    /** Status line about the body model (person mode). */
    static String bodyModelText(String state) {
        if ("ok".equals(state)) return "体のモデル: あり（明るいときだけ確認）";
        if ("none".equals(state)) return "体のモデルなし（顔と形だけで判定）";
        if ("failed".equals(state)) return "体のモデル: 使えません（顔と形だけで判定）";
        return "";
    }

    private String extraStatusJson(CameraPipeline p) {
        MonitorPolicy.Decision d = decision;
        MonitorPolicy.Config c = policy.config();
        StringBuilder sb = new StringBuilder();
        sb.append(",\"mode\":\"").append(settings.detectMode).append('"');
        sb.append(",\"person\":{\"bodyModel\":\"").append(p == null ? "" : p.bodyModelState()).append("\",\"present\":").append(p != null && p.personPresent())
                .append(",\"lastSeen\":").append(p == null ? 0L : p.lastPersonWallMs())
                .append(",\"basis\":").append(HttpServer.jsonString(p == null ? "" : p.personBasis())).append('}');
        sb.append(",\"policy\":{\"active\":").append(d == null || d.active())
                .append(",\"primary\":").append(d == null || d.primary == null ? "null" : "\"" + d.primary.key + "\"")
                .append(",\"reasons\":").append(d == null ? "[]" : d.reasonsJson())
                .append(",\"text\":").append(d == null || d.text == null ? "null" : HttpServer.jsonString(d.text))
                .append(",\"lowBattery\":{\"pauseBelow\":").append(c.lowPausePct).append(",\"resumeAt\":").append(c.lowResumePct)
                .append(",\"latched\":").append(policy.lowLatched()).append('}')
                .append(",\"quiet\":{\"enabled\":").append(c.quietEnabled)
                .append(",\"start\":\"").append(QuietHours.format(c.quietStartMin)).append("\",\"end\":\"")
                .append(QuietHours.format(c.quietEndMin)).append("\",\"keepPct\":").append(c.quietKeepPct)
                .append(",\"paused\":").append(policy.quietPaused()).append("}}");
        sb.append(",\"update\":").append(updates == null ? "null" : updates.json());
        return sb.toString();
    }

    @Override
    public boolean monitoring() {
        return monitoring;
    }

    /** Switches monitoring; callable from any thread (the camera is started/stopped on the main thread). */
    @Override
    public void setMonitoring(final boolean on) {
        monitoring = on; // only the user's own switch; heat, battery and sleep time are separate reasons
        PreferenceManager.getDefaultSharedPreferences(this).edit().putBoolean(PREF_MONITORING, on).apply();
        main.post(new Runnable() {
            @Override
            public void run() {
                applyPolicy();
            }
        });
    }

    UpdateManager updates() {
        return updates;
    }

    @Override
    public String updateJson() {
        return updates == null ? "null" : updates.json();
    }

    @Override
    public String updateAction(String action) {
        if (updates == null) return "更新機能を利用できません";
        if ("check".equals(action)) return updates.check(true);
        if ("download".equals(action)) return updates.download();
        if ("install".equals(action)) return updates.install();
        return "不明な操作です";
    }

    @Override
    public UpdatePush.Result acceptUpdate(String code, InputStream body, long length) {
        if (updates == null) return UpdatePush.Result.fail(503, "更新機能を利用できません");
        return updates.acceptPush(code, body, length);
    }

    @Override
    public String settingsJson() {
        return settingsApi.json();
    }

    @Override
    public String updateSettings(java.util.Map<String, String> changes) {
        String error = settingsApi.update(changes);
        if (error == null) {
            // Apply after the HTTP reply has gone out: a port change restarts the server.
            main.removeCallbacks(reload);
            main.postDelayed(reload, 800);
        }
        return error;
    }

    @Override
    public String uptimeJson() {
        return uptime.json();
    }

    @Override
    public RecordingStore store() {
        return store;
    }

    @Override
    public FrameHub frames() {
        return hub;
    }

    @Override
    public String password() {
        return settings.password;
    }
}
