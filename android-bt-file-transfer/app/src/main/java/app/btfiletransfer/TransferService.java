package app.btfiletransfer;

import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothServerSocket;
import android.bluetooth.BluetoothSocket;
import android.content.BroadcastReceiver;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.StatFs;
import android.provider.OpenableColumns;
import app.btfiletransfer.obex.ObexConstants;
import app.btfiletransfer.obex.ObexException;
import app.btfiletransfer.obex.ObexPushClient;
import app.btfiletransfer.obex.ObexPushServer;
import app.btfiletransfer.storage.FileNames;
import app.btfiletransfer.storage.ReceivedFileStore;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * アプリ起動中に常駐し、Windows 10 の「Bluetooth 経由でファイルを送受信」の相手として振る舞う。
 * <ul>
 *   <li>受信: OBEX Object Push (UUID 0x1105) の SDP レコードを登録して RFCOMM で待ち受け</li>
 *   <li>送信: ペアリング済み PC の OPP サーバ（Windows 側「ファイルを受信する」画面）へ PUT</li>
 * </ul>
 *
 * <p>Bluetooth 権限は起動時に {@link MainActivity} がまとめて確認し、許可されない場合はこのサービスを
 * 開始しない。Android 12+ で実行時に取り消された場合は SecurityException として個別に扱う。
 */
@SuppressLint("MissingPermission")
public class TransferService extends Service {
    public static final String ACTION_STOP = "app.btfiletransfer.STOP";

    private static final String CHANNEL_ID = "transfer";
    private static final int NOTIFICATION_ID = 1;
    private static final int MAX_LOG = 200;

    /** 画面側の購読者。コールバックは常にメインスレッドで呼ばれる。 */
    public interface Listener {
        void onStateChanged();
    }

    /** 進行中の転送。 */
    public static final class Progress {
        public final String label;
        public final long done;
        public final long total;

        Progress(String label, long done, long total) {
            this.label = label;
            this.done = done;
            this.total = total;
        }
    }

    public final class LocalBinder extends Binder {
        public TransferService service() {
            return TransferService.this;
        }
    }

    private final IBinder binder = new LocalBinder();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final List<Listener> listeners = new CopyOnWriteArrayList<>();
    private final List<LogEntry> log = new ArrayList<>();
    private final ExecutorService sendExecutor = Executors.newSingleThreadExecutor();

    private BluetoothAdapter adapter;
    private ReceivedFileStore store;
    private SystemReceiveWatcher systemWatcher;

    private volatile BluetoothServerSocket serverSocket;
    private Thread acceptThread;
    private volatile String serverStatus = "";
    private volatile boolean serverListening;
    private volatile Progress progress;
    private long lastProgressUpdate;

    /** 通知の「停止」で終了中。画面側はこれを見て自身も閉じる（バインド中はサービスが破棄されないため）。 */
    private volatile boolean stopping;

    private volatile ObexPushClient activeClient;
    private volatile BluetoothSocket activeSendSocket;

    private final BroadcastReceiver btReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction())) {
                int state = intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR);
                if (state == BluetoothAdapter.STATE_ON) {
                    startServer();
                } else if (state == BluetoothAdapter.STATE_TURNING_OFF || state == BluetoothAdapter.STATE_OFF) {
                    stopServer(getString(R.string.status_bt_off));
                }
            }
            notifyChanged();
        }
    };

    // ------------------------------------------------------------------ lifecycle

    @Override
    public void onCreate() {
        super.onCreate();
        adapter = ((BluetoothManager) getSystemService(BLUETOOTH_SERVICE)).getAdapter();
        store = new ReceivedFileStore(this);
        createChannel();
        IntentFilter f = new IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED);
        f.addAction(BluetoothAdapter.ACTION_SCAN_MODE_CHANGED);
        f.addAction(BluetoothAdapter.ACTION_LOCAL_NAME_CHANGED);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(btReceiver, f, RECEIVER_NOT_EXPORTED); // システムブロードキャストのみ受ける
        } else {
            registerReceiver(btReceiver, f);
        }
        if (Build.VERSION.SDK_INT < 29) {
            systemWatcher = new SystemReceiveWatcher(file -> addLog(
                    getString(R.string.log_system_received, file.getName(), file.getParent()),
                    null, null, false));
            systemWatcher.start();
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopping = true;
            cancelSend();
            stopServer(getString(R.string.status_stopped));
            removeForeground();
            stopSelf();
            return START_NOT_STICKY;
        }
        goForeground();
        startServer();
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public void onDestroy() {
        unregisterReceiver(btReceiver);
        if (systemWatcher != null) systemWatcher.stop();
        stopping = true;
        cancelSend();
        stopServer(getString(R.string.status_stopped));
        sendExecutor.shutdownNow();
        removeForeground();
        super.onDestroy();
    }

    private void removeForeground() {
        if (Build.VERSION.SDK_INT >= 24) {
            stopForeground(STOP_FOREGROUND_REMOVE);
        } else {
            //noinspection deprecation
            stopForeground(true);
        }
    }

    // ------------------------------------------------------------------ 画面向け API

    public void addListener(Listener l) {
        listeners.add(l);
    }

    public void removeListener(Listener l) {
        listeners.remove(l);
    }

    public boolean isStopping() {
        return stopping;
    }

    public boolean isServerListening() {
        return serverListening;
    }

    public String serverStatus() {
        return serverStatus;
    }

    public Progress progress() {
        return progress;
    }

    public String saveFolder() {
        return store.folderDescription();
    }

    public List<LogEntry> logSnapshot() {
        synchronized (log) {
            return new ArrayList<>(log);
        }
    }

    public void sendFiles(final BluetoothDevice device, final List<Uri> uris) {
        sendExecutor.execute(() -> doSend(device, uris));
    }

    public boolean isSending() {
        return activeSendSocket != null;
    }

    public void cancelSend() {
        ObexPushClient c = activeClient;
        if (c != null) c.cancel();
        BluetoothSocket s = activeSendSocket;
        if (s != null) closeQuietly(s);
    }

    // ------------------------------------------------------------------ 受信（Windows → 端末）

    private synchronized void startServer() {
        if (acceptThread != null || stopping) return;
        if (adapter == null || !adapter.isEnabled()) {
            serverStatus = getString(R.string.status_bt_off);
            notifyChanged();
            return;
        }
        final BluetoothServerSocket ss;
        try {
            // secure（認証・暗号化あり）= ペアリング済みの相手だけが接続できる。
            // UUID 0x1105 を指定すると Android のスタックが OPP 用の SDP レコードを登録する
            ss = adapter.listenUsingRfcommWithServiceRecord("OBEX Object Push", ObexConstants.OPP_UUID);
        } catch (IOException | SecurityException e) {
            serverListening = false;
            serverStatus = getString(R.string.status_listen_failed, String.valueOf(e.getMessage()));
            addLog(serverStatus, null, null, true);
            return;
        }
        serverSocket = ss;
        serverListening = true;
        serverStatus = getString(R.string.status_listening);
        acceptThread = new Thread(() -> acceptLoop(ss), "opp-accept");
        acceptThread.start();
        updateNotification();
        notifyChanged();
    }

    private synchronized void stopServer(String status) {
        BluetoothServerSocket ss = serverSocket;
        serverSocket = null;
        if (ss != null) {
            try {
                ss.close();
            } catch (IOException ignored) {
                // accept() を中断させるための close なので無視
            }
        }
        acceptThread = null;
        serverListening = false;
        serverStatus = status;
        notifyChanged();
    }

    private void acceptLoop(BluetoothServerSocket ss) {
        while (serverSocket == ss) {
            final BluetoothSocket socket;
            try {
                socket = ss.accept();
            } catch (IOException e) {
                if (serverSocket == ss) {
                    // close() 以外の理由で待ち受けが落ちた → 作り直す
                    synchronized (this) {
                        serverSocket = null;
                        acceptThread = null;
                        serverListening = false;
                    }
                    try {
                        ss.close();
                    } catch (IOException ignored) {
                        // 既に壊れているソケットの後始末
                    }
                    main.postDelayed(this::startServer, 2000);
                }
                return;
            }
            new Thread(() -> handleIncoming(socket), "opp-session").start();
        }
    }

    private void handleIncoming(BluetoothSocket socket) {
        final String peer = deviceLabel(socket.getRemoteDevice());
        try {
            ObexPushServer server = new ObexPushServer(socket.getInputStream(), socket.getOutputStream(),
                    (name, mime, length) -> beginReceive(peer, name, mime, length));
            server.run();
        } catch (IOException e) {
            // 受信途中の切断は Sink.abort() 側で記録済み
        } finally {
            closeQuietly(socket);
            setProgress(null);
        }
    }

    private ObexPushServer.Sink beginReceive(final String peer, String rawName, String mime, final long length)
            throws IOException {
        final String name = FileNames.sanitize(rawName,
                "received_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()));
        if (length > 0 && length > freeBytes()) {
            addLog(getString(R.string.log_no_space, name), null, null, true);
            throw new ObexException(ObexConstants.RSP_ENTITY_TOO_LARGE, "空き容量不足");
        }
        final String mimeType = (mime == null || mime.isEmpty()) ? ReceivedFileStore.guessMime(name) : mime;
        final ReceivedFileStore.Pending pending = store.create(name, mimeType);
        final String label = getString(R.string.progress_receiving, name, peer);
        setProgress(new Progress(label, 0, length));
        return new ObexPushServer.Sink() {
            long received;

            @Override
            public void write(byte[] b, int off, int len) throws IOException {
                pending.stream().write(b, off, len);
                received += len;
                maybeUpdateProgress(label, received, length);
            }

            @Override
            public void commit() throws IOException {
                Uri uri = pending.commit();
                setProgress(null);
                addLog(getString(R.string.log_received, name, peer, formatSize(received), pending.location()),
                        uri, mimeType, false);
            }

            @Override
            public void abort() {
                pending.abort();
                setProgress(null);
                addLog(getString(R.string.log_receive_aborted, name, peer), null, null, true);
            }
        };
    }

    private static long freeBytes() {
        try {
            return new StatFs(Environment.getExternalStorageDirectory().getPath()).getAvailableBytes();
        } catch (IllegalArgumentException e) {
            return Long.MAX_VALUE;
        }
    }

    // ------------------------------------------------------------------ 送信（端末 → Windows）

    private void doSend(BluetoothDevice device, List<Uri> uris) {
        String peer = deviceLabel(device);
        BluetoothSocket socket = null;
        int sentCount = 0;
        try {
            try {
                adapter.cancelDiscovery(); // 検索中は接続が極端に遅くなる
            } catch (SecurityException ignored) {
                // Android 12+ で BLUETOOTH_SCAN が無い場合。接続自体は可能
            }
            setProgress(new Progress(getString(R.string.progress_connecting, peer), 0, -1));
            socket = device.createRfcommSocketToServiceRecord(ObexConstants.OPP_UUID);
            activeSendSocket = socket;
            try {
                socket.connect();
            } catch (IOException e) {
                addLog(getString(R.string.log_connect_failed, peer), null, null, true);
                return;
            }
            ObexPushClient client = new ObexPushClient(socket.getInputStream(), socket.getOutputStream());
            activeClient = client;
            client.connect();
            ContentResolver cr = getContentResolver();
            for (Uri uri : uris) {
                String[] meta = queryNameAndSize(cr, uri);
                final String name = FileNames.sanitize(meta[0], "file");
                final long size = meta[1] == null ? -1 : Long.parseLong(meta[1]);
                String mime = cr.getType(uri);
                if (mime == null) mime = ReceivedFileStore.guessMime(name);
                final String label = getString(R.string.progress_sending, name, peer);
                setProgress(new Progress(label, 0, size));
                InputStream in = cr.openInputStream(uri);
                if (in == null) throw new IOException("読み込めません: " + uri);
                try {
                    client.put(name, mime, size, in, (sent, total) -> maybeUpdateProgress(label, sent, total));
                } finally {
                    in.close();
                }
                sentCount++;
                addLog(getString(R.string.log_sent, name, peer, size >= 0 ? formatSize(size) : "?"),
                        null, null, false);
            }
            client.disconnect();
        } catch (ObexException e) {
            addLog(getString(R.string.log_send_rejected, peer, e.getMessage()), null, null, true);
        } catch (IOException | SecurityException | IllegalArgumentException e) {
            addLog(getString(R.string.log_send_failed, peer, sentCount, uris.size(), String.valueOf(e.getMessage())),
                    null, null, true);
        } finally {
            activeClient = null;
            activeSendSocket = null;
            if (socket != null) closeQuietly(socket);
            setProgress(null);
        }
    }

    /** [表示名, サイズ(文字列 or null)] */
    private static String[] queryNameAndSize(ContentResolver cr, Uri uri) {
        String name = uri.getLastPathSegment();
        String size = null;
        Cursor c = null;
        try {
            c = cr.query(uri, new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null);
            if (c != null && c.moveToFirst()) {
                int ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                int si = c.getColumnIndex(OpenableColumns.SIZE);
                if (ni >= 0 && !c.isNull(ni)) name = c.getString(ni);
                if (si >= 0 && !c.isNull(si)) size = String.valueOf(c.getLong(si));
            }
        } catch (RuntimeException ignored) {
            // file:// など query 非対応の Uri
        } finally {
            if (c != null) c.close();
        }
        if (size == null && "file".equals(uri.getScheme()) && uri.getPath() != null) {
            File f = new File(uri.getPath());
            if (f.isFile()) size = String.valueOf(f.length());
        }
        return new String[] {name, size};
    }

    // ------------------------------------------------------------------ 状態・通知

    private void setProgress(Progress p) {
        progress = p;
        lastProgressUpdate = System.currentTimeMillis();
        updateNotification();
        notifyChanged();
    }

    private void maybeUpdateProgress(String label, long done, long total) {
        long now = System.currentTimeMillis();
        if (now - lastProgressUpdate < 300) return;
        setProgress(new Progress(label, done, total));
    }

    private void addLog(String text, Uri uri, String mime, boolean error) {
        synchronized (log) {
            log.add(0, new LogEntry(text, uri, mime, error));
            while (log.size() > MAX_LOG) log.remove(log.size() - 1);
        }
        notifyChanged();
    }

    private void notifyChanged() {
        main.post(() -> {
            for (Listener l : listeners) l.onStateChanged();
        });
    }

    String deviceLabel(BluetoothDevice d) {
        try {
            String n = d.getName();
            return n != null ? n : d.getAddress();
        } catch (SecurityException e) {
            return d.getAddress();
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, getString(R.string.channel_name),
                NotificationManager.IMPORTANCE_LOW);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }

    private Notification buildNotification() {
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        int immutable = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                immutable | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, TransferService.class).setAction(ACTION_STOP),
                immutable | PendingIntent.FLAG_UPDATE_CURRENT);
        Progress p = progress;
        b.setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(p != null ? p.label : serverStatus)
                .setContentIntent(open)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .addAction(0, getString(R.string.action_stop), stop);
        if (p != null) {
            if (p.total > 0) {
                b.setProgress(1000, (int) (p.done * 1000 / Math.max(1, p.total)), false);
            } else {
                b.setProgress(0, 0, true);
            }
        }
        return b.build();
    }

    private void goForeground() {
        Notification n = buildNotification();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        } else {
            startForeground(NOTIFICATION_ID, n);
        }
    }

    private void updateNotification() {
        if (stopping) return;
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        try {
            nm.notify(NOTIFICATION_ID, buildNotification());
        } catch (SecurityException ignored) {
            // Android 13+ で通知権限が無い場合
        }
    }

    static String formatSize(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format(Locale.US, "%.1f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) return String.format(Locale.US, "%.1f MB", bytes / 1048576.0);
        return String.format(Locale.US, "%.2f GB", bytes / 1073741824.0);
    }

    private static void closeQuietly(BluetoothSocket s) {
        try {
            s.close();
        } catch (IOException ignored) {
            // 後始末なので無視
        }
    }
}
