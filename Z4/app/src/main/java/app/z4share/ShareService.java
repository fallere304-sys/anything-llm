package app.z4share;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.database.Cursor;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Binder;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.OpenableColumns;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.SecureRandom;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * アプリ起動中ずっと HTTP サーバーを動かしておくフォアグラウンドサービス。
 * 画面が消えても転送が止まらないように WakeLock と WifiLock を保持する。
 */
public class ShareService extends Service {

    static final String ACTION_STOP = "app.z4share.STOP";
    private static final String TAG = "Z4Share";
    private static final String CHANNEL_ID = "server";
    private static final int NOTIFICATION_ID = 1;
    private static final int PREFERRED_PORT = 8765;

    interface UiListener {
        void onFilesChanged();

        void onServiceStopped();
    }

    final class LocalBinder extends Binder {
        ShareService getService() {
            return ShareService.this;
        }
    }

    private final IBinder binder = new LocalBinder();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService copier = Executors.newSingleThreadExecutor();
    private UiListener uiListener;

    private Z4HttpServer server;
    private String token;
    private int port = -1;
    private String startError;
    private File outboxDir;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private String lastReceived;

    @Override
    public void onCreate() {
        super.onCreate();
        startForegroundCompat(buildNotification());

        token = randomToken();
        outboxDir = new File(getFilesDir(), "outbox");
        //noinspection ResultOfMethodCallIgnored
        outboxDir.mkdirs();

        byte[] html;
        try (InputStream in = getAssets().open("index.html")) {
            html = Z4HttpServer.readAll(in);
        } catch (IOException e) {
            throw new IllegalStateException("index.html missing", e);
        }
        server = new Z4HttpServer(outboxDir, chooseInboxDir(), token, html,
                Build.MANUFACTURER + " " + Build.MODEL, new Z4HttpServer.Listener() {
            @Override
            public void onFileReceived(final File file) {
                main.post(new Runnable() {
                    @Override
                    public void run() {
                        onReceived(file);
                    }
                });
            }

            @Override
            public void onError(String message) {
                Log.w(TAG, "server: " + message);
            }
        });
        try {
            port = server.start(PREFERRED_PORT);
        } catch (IOException e) {
            startError = e.getMessage();
            Log.e(TAG, "server start failed", e);
        }
        acquireLocks();
        updateNotification();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            // 画面が bind したままだと stopSelf だけでは終わらないので、先に画面を閉じさせる
            stopForeground(true);
            if (uiListener != null) {
                uiListener.onServiceStopped();
            }
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public void onDestroy() {
        if (server != null) {
            server.stop();
        }
        copier.shutdownNow();
        releaseLocks();
        stopForeground(true);
        if (uiListener != null) {
            uiListener.onServiceStopped();
        }
        super.onDestroy();
    }

    // ---------------------------------------------------------------- API for MainActivity

    void setUiListener(UiListener l) {
        uiListener = l;
    }

    /** サーバーの待ち受けポート。起動に失敗していれば -1。 */
    int getPort() {
        return port;
    }

    String getStartError() {
        return startError;
    }

    String getToken() {
        return token;
    }

    List<File> getOutboxFiles() {
        return Z4HttpServer.listFiles(outboxDir);
    }

    List<File> getReceivedFiles() {
        return Z4HttpServer.listFiles(server.getInboxDir());
    }

    File getInboxDir() {
        return server.getInboxDir();
    }

    boolean isInboxPublicDownloads() {
        return getInboxDir().getAbsolutePath().startsWith(
                Environment.getExternalStorageDirectory().getAbsolutePath() + "/" + Environment.DIRECTORY_DOWNLOADS);
    }

    /** ストレージ権限を得た後などに受信フォルダを選び直す。 */
    void refreshInboxDir() {
        server.setInboxDir(chooseInboxDir());
        notifyFilesChanged();
    }

    void removeFromOutbox(File f) {
        //noinspection ResultOfMethodCallIgnored
        f.delete();
        notifyFilesChanged();
    }

    /** 他アプリの共有やファイル選択で受け取った URI をアプリ内の送信フォルダへコピーする。 */
    void addToOutbox(final List<Uri> uris, final Runnable onDone) {
        copier.execute(new Runnable() {
            @Override
            public void run() {
                ContentResolver cr = getContentResolver();
                for (Uri uri : uris) {
                    String name = Z4HttpServer.sanitizeFileName(displayName(cr, uri));
                    File dest = Z4HttpServer.uniqueFile(outboxDir, name);
                    File tmp = new File(outboxDir, "." + dest.getName() + Z4HttpServer.PART_SUFFIX);
                    try (InputStream in = cr.openInputStream(uri);
                         OutputStream out = new FileOutputStream(tmp)) {
                        if (in == null) {
                            throw new IOException("cannot open " + uri);
                        }
                        byte[] buf = new byte[64 * 1024];
                        int n;
                        while ((n = in.read(buf)) != -1) {
                            out.write(buf, 0, n);
                        }
                    } catch (IOException | SecurityException e) {
                        Log.w(TAG, "copy failed: " + uri, e);
                        //noinspection ResultOfMethodCallIgnored
                        tmp.delete();
                        continue;
                    }
                    if (!tmp.renameTo(dest)) {
                        //noinspection ResultOfMethodCallIgnored
                        tmp.delete();
                    }
                }
                main.post(new Runnable() {
                    @Override
                    public void run() {
                        notifyFilesChanged();
                        if (onDone != null) {
                            onDone.run();
                        }
                    }
                });
            }
        });
    }

    // ---------------------------------------------------------------- internals

    private void onReceived(File file) {
        lastReceived = file.getName();
        MediaScannerConnection.scanFile(this, new String[]{file.getAbsolutePath()}, null, null);
        updateNotification();
        notifyFilesChanged();
    }

    private void notifyFilesChanged() {
        if (uiListener != null) {
            uiListener.onFilesChanged();
        }
    }

    /**
     * Android 9 以下で書き込み権限があれば「Download/Z4Share」(他アプリからも見える)。
     * それ以外はアプリ専用フォルダ (Android/data/app.z4share/files/Download)。
     */
    private File chooseInboxDir() {
        if (Build.VERSION.SDK_INT <= 28
                && Environment.MEDIA_MOUNTED.equals(Environment.getExternalStorageState())
                && hasWritePermission()) {
            File dir = new File(Environment.getExternalStoragePublicDirectory(
                    Environment.DIRECTORY_DOWNLOADS), "Z4Share");
            if (dir.isDirectory() || dir.mkdirs()) {
                return dir;
            }
        }
        File dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (dir == null) {
            dir = new File(getFilesDir(), "received");
        }
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        return dir;
    }

    private boolean hasWritePermission() {
        return Build.VERSION.SDK_INT < 23
                || checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED;
    }

    private static String displayName(ContentResolver cr, Uri uri) {
        if ("content".equals(uri.getScheme())) {
            try (Cursor c = cr.query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
                if (c != null && c.moveToFirst() && !c.isNull(0)) {
                    return c.getString(0);
                }
            } catch (RuntimeException ignored) {
                // 名前が取れない提供元もある
            }
        }
        String last = uri.getLastPathSegment();
        return last != null ? last : "file";
    }

    private static String randomToken() {
        byte[] b = new byte[16];
        new SecureRandom().nextBytes(b);
        StringBuilder sb = new StringBuilder();
        for (byte x : b) {
            sb.append(String.format(Locale.US, "%02x", x & 0xff));
        }
        return sb.toString();
    }

    @SuppressWarnings("deprecation")
    private void acquireLocks() {
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Z4Share:server");
        wakeLock.acquire();
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wm != null) {
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "Z4Share:server");
            wifiLock.acquire();
        }
    }

    private void releaseLocks() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
    }

    // ---------------------------------------------------------------- notification

    private void startForegroundCompat(Notification n) {
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, n);
        }
    }

    private void updateNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        nm.notify(NOTIFICATION_ID, buildNotification());
    }

    @SuppressWarnings("deprecation")
    private Notification buildNotification() {
        int immutable = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), immutable | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, ShareService.class).setAction(ACTION_STOP),
                immutable | PendingIntent.FLAG_UPDATE_CURRENT);

        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            nm.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, "受信待ち", NotificationManager.IMPORTANCE_LOW));
            b = new Notification.Builder(this, CHANNEL_ID);
        } else {
            b = new Notification.Builder(this);
        }
        String text = lastReceived != null ? "受信しました: " + lastReceived : "iPhone からの接続を待っています";
        return b.setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("Z4 Share 起動中")
                .setContentText(text)
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "停止", stop)
                .build();
    }
}
