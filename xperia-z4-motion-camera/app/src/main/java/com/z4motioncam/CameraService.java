package com.z4motioncam;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.DhcpInfo;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.Binder;
import android.os.Environment;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
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
    private volatile NetworkDiagnostics.Result diag;
    private SSLSocketFactory clientTls;
    private HttpServer http;
    private String httpError;
    private byte[] indexHtml;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;

    private volatile ThermalPolicy.Level thermal = ThermalPolicy.Level.NORMAL;
    private volatile float batteryTempC = Float.NaN;
    private volatile int batteryPct = -1;
    private volatile boolean charging;

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

        try {
            InputStream in = getAssets().open("cacerts.pem");
            try {
                clientTls = NetUtil.clientTls(NetUtil.parsePem(new String(HttpServer.readAll(in), "UTF-8")));
            } finally {
                in.close();
            }
        } catch (Exception e) {
            Log.w(TAG, "extra roots unavailable", e);
        }

        applySettings(AppSettings.load(this));
        registerReceiver(networkReceiver, new IntentFilter(ConnectivityManager.CONNECTIVITY_ACTION));
        // Sticky broadcast: delivers the current state immediately, then every change.
        registerReceiver(batteryReceiver, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_RELOAD.equals(intent.getAction())) {
            AppSettings s = AppSettings.load(this);
            if (!s.equals(settings)) applySettings(s);
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public void onDestroy() {
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
        remote = new RemoteAccess(this, s, this, clientTls);
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
        sb.append(String.format(Locale.US, "電池 %.1f℃ %s%s  負荷制御:%s", batteryTempC,
                batteryPct < 0 ? "" : batteryPct + "%", charging ? "(充電中)" : "", thermal));
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
                "{\"state\":\"%s\",\"motion\":%.4f,\"lastMotion\":%d,\"tempC\":%.1f,\"battery\":%d,"
                        + "\"charging\":%b,\"thermal\":\"%s\",\"freeBytes\":%d,\"storageFull\":%b,"
                        + "\"rotation\":%d,\"viewers\":%d,\"error\":%s,\"remote\":%s}",
                state, p == null ? 0f : p.motionRatio(), p == null ? 0L : p.lastMotionWallMs(),
                Float.isNaN(batteryTempC) ? 0f : batteryTempC, batteryPct, charging, thermal,
                store.usableBytes(), p != null && p.storageFull(), settings.rotation, hub.clients(),
                err == null ? "null" : HttpServer.jsonString(err),
                remote == null ? "null" : remote.statusJson());
    }

    @Override
    public synchronized String netDiagJson(boolean refresh) {
        NetworkDiagnostics.Result d = diag;
        if (refresh || d == null || System.currentTimeMillis() - d.checkedAt > 5 * 60_000L) {
            d = NetworkDiagnostics.run(gatewayIpv4(), clientTls);
            diag = d;
        }
        return d.toJson(settings.remotePort);
    }

    private String gatewayIpv4() {
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        DhcpInfo dhcp = wm == null ? null : wm.getDhcpInfo();
        if (dhcp == null || dhcp.gateway == 0) return null;
        int g = dhcp.gateway; // little-endian
        return (g & 0xFF) + "." + ((g >> 8) & 0xFF) + "." + ((g >> 16) & 0xFF) + "." + ((g >> 24) & 0xFF);
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
