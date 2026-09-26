package com.z4motioncam;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.Binder;
import android.os.Environment;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

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

    private volatile ThermalPolicy.Level thermal = ThermalPolicy.Level.NORMAL;
    private volatile float batteryTempC = Float.NaN;
    private volatile int batteryPct = -1;
    private volatile boolean charging;
    private volatile int batteryVoltageMv = -1;
    private volatile String powerState = "";
    private volatile String batteryHealth = "";
    private UptimeLog uptime;
    private SettingsApi settingsApi;
    private final Handler main = new Handler(Looper.getMainLooper());

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
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Z4MotionCam:camera");
        wakeLock.acquire();
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

        applySettings(AppSettings.load(this));
        registerReceiver(networkReceiver, new IntentFilter(ConnectivityManager.CONNECTIVITY_ACTION));
        // Sticky broadcast: returns the current state now, then delivers every change.
        Intent battery = registerReceiver(batteryReceiver, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (battery != null) onBattery(battery);

        settingsApi = new SettingsApi(this);
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
        uptime.begin(snapshot());
        registerReceiver(shutdownReceiver, new IntentFilter(Intent.ACTION_SHUTDOWN));
        main.postDelayed(heartbeat, UptimeLog.BEAT_MS);
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
        if (thermal != ThermalPolicy.Level.CRITICAL) startPipeline();
        if (s.ddnsConfigured() && ddnsTls == null) ddnsTls = loadDdnsTls();
        remote = new RemoteAccess(this, s, this, ddnsTls);
        remote.start();
    }

    private void startPipeline() {
        if (pipeline != null) return;
        pipeline = new CameraPipeline(settings, store, hub);
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
        batteryPct = level < 0 || scale <= 0 ? -1 : level * 100 / scale;
        charging = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
        batteryVoltageMv = i.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1);
        int status = i.getIntExtra(BatteryManager.EXTRA_STATUS, BatteryManager.BATTERY_STATUS_UNKNOWN);
        if (!charging) powerState = "未接続";
        else if (status == BatteryManager.BATTERY_STATUS_FULL) powerState = "満充電";
        else if (status == BatteryManager.BATTERY_STATUS_CHARGING) powerState = "充電中";
        else powerState = "接続中・充電停止"; // plugged in but the charger stopped (e.g. battery too hot)
        batteryHealth = healthText(i.getIntExtra(BatteryManager.EXTRA_HEALTH, BatteryManager.BATTERY_HEALTH_UNKNOWN));
        if (t == Integer.MIN_VALUE) return;
        batteryTempC = t / 10f;

        ThermalPolicy.Level prev = thermal;
        ThermalPolicy.Level next = ThermalPolicy.next(prev, batteryTempC);
        if (next == prev) return;
        thermal = next;
        Log.w(TAG, "thermal " + prev + " -> " + next + " (" + batteryTempC + "C)");
        if (next == ThermalPolicy.Level.CRITICAL) {
            // Last resort: release the camera entirely until the phone cools down.
            stopPipeline();
        } else {
            if (pipeline == null) startPipeline();
            pipeline.setThermalLevel(next);
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
        if (p == null) {
            sb.append(thermal == ThermalPolicy.Level.CRITICAL ? "高温のため一時停止中（冷却待ち）" : "停止中");
        } else if (!p.isRunning()) {
            sb.append("カメラ準備中");
        } else {
            sb.append(p.isRecording() ? "● 録画中" : "監視中");
            sb.append(String.format(Locale.US, "  動き %.1f%%", p.motionRatio() * 100));
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
        if (p == null) state = thermal == ThermalPolicy.Level.CRITICAL ? "cooling" : "stopped";
        else if (!p.isRunning()) state = "starting";
        else state = p.isRecording() ? "recording" : "watching";
        String err = p == null ? null : p.error();
        return String.format(Locale.US,
                "{\"state\":\"%s\",\"motion\":%.4f,\"lastMotion\":%d,\"tempC\":%.1f,\"battery\":%d,\"power\":%s,"
                        + "\"charging\":%b,\"thermal\":\"%s\",\"freeBytes\":%d,\"storageFull\":%b,"
                        + "\"rotation\":%d,\"viewers\":%d,\"error\":%s,\"remote\":%s}",
                state, p == null ? 0f : p.motionRatio(), p == null ? 0L : p.lastMotionWallMs(),
                Float.isNaN(batteryTempC) ? 0f : batteryTempC, batteryPct, HttpServer.jsonString(powerState), charging, thermal,
                store.usableBytes(), p != null && p.storageFull(), settings.rotation, hub.clients(),
                err == null ? "null" : HttpServer.jsonString(err),
                remote == null ? "null" : remote.statusJson());
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
