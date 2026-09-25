package app.btfiletransfer;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothClass;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcelable;
import android.text.format.DateFormat;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;

/**
 * 画面。表示中（＝アプリ起動中）は {@link TransferService} を前面サービスとして動かし、
 * 「戻る」「終了」で画面を閉じるとサービスも止める。ホームボタンで離れた場合は待機を続ける。
 *
 * <p>Bluetooth 権限は {@link #requestMissingPermissions()} で確認済みの前提で API を呼ぶ。
 */
@SuppressLint("MissingPermission")
public class MainActivity extends Activity implements TransferService.Listener {
    private static final int REQ_PERMISSIONS = 1;
    private static final int REQ_ENABLE_BT = 2;
    private static final int REQ_DISCOVERABLE = 3;
    private static final int REQ_PICK_FILES = 4;

    private static final int DISCOVERABLE_SECONDS = 300;
    private static final String PREFS = "prefs";
    private static final String PREF_LAST_DEVICE = "last_device";
    private static final String STATE_AUTO_DISCOVERABLE = "auto_discoverable_done";

    private final Handler main = new Handler(Looper.getMainLooper());
    private BluetoothAdapter adapter;
    private TransferService service;
    private boolean bound;
    private boolean autoDiscoverableDone;
    private long discoverableUntil;
    /** 共有インテントやファイル選択で受け取り、送信先選択待ちの Uri。 */
    private ArrayList<Uri> pendingUris;

    private TextView serverStatus;
    private TextView deviceName;
    private TextView discoverable;
    private TextView saveFolder;
    private View progressGroup;
    private TextView progressLabel;
    private ProgressBar progressBar;
    private ListView history;
    private View historyEmpty;
    private HistoryAdapter historyAdapter;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = ((TransferService.LocalBinder) binder).service();
            service.addListener(MainActivity.this);
            onStateChanged();
            if (pendingUris != null) chooseDeviceAndSend();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            service = null;
        }
    };

    private final Runnable ticker = new Runnable() {
        @Override
        public void run() {
            renderDiscoverable();
            main.postDelayed(this, 1000);
        }
    };

    // ------------------------------------------------------------------ lifecycle

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        serverStatus = findViewById(R.id.serverStatus);
        deviceName = findViewById(R.id.deviceName);
        discoverable = findViewById(R.id.discoverable);
        saveFolder = findViewById(R.id.saveFolder);
        progressGroup = findViewById(R.id.progressGroup);
        progressLabel = findViewById(R.id.progressLabel);
        progressBar = findViewById(R.id.progressBar);
        history = findViewById(R.id.history);
        historyEmpty = findViewById(R.id.historyEmpty);
        historyAdapter = new HistoryAdapter(this);
        history.setAdapter(historyAdapter);
        history.setEmptyView(historyEmpty);
        history.setOnItemClickListener((parent, view, position, id) -> openEntry(historyAdapter.getItem(position)));

        findViewById(R.id.buttonDiscoverable).setOnClickListener(v -> requestDiscoverable());
        findViewById(R.id.buttonSend).setOnClickListener(v -> pickFiles());
        findViewById(R.id.buttonHelp).setOnClickListener(v -> showHelp());
        findViewById(R.id.buttonExit).setOnClickListener(v -> finish());
        findViewById(R.id.buttonCancel).setOnClickListener(v -> {
            if (service != null) service.cancelSend();
        });

        if (savedInstanceState != null) {
            autoDiscoverableDone = savedInstanceState.getBoolean(STATE_AUTO_DISCOVERABLE);
        }
        adapter = ((BluetoothManager) getSystemService(BLUETOOTH_SERVICE)).getAdapter();
        if (adapter == null) {
            serverStatus.setText(R.string.status_no_bt);
            setButtonsEnabled(false);
            return;
        }
        takeSharedUris(getIntent());
        requestMissingPermissions();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (takeSharedUris(intent) && service != null) chooseDeviceAndSend();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        outState.putBoolean(STATE_AUTO_DISCOVERABLE, autoDiscoverableDone);
    }

    @Override
    protected void onResume() {
        super.onResume();
        main.post(ticker);
        onStateChanged();
    }

    @Override
    protected void onPause() {
        super.onPause();
        main.removeCallbacks(ticker);
    }

    @Override
    protected void onDestroy() {
        if (bound) {
            if (service != null) service.removeListener(this);
            unbindService(connection);
            bound = false;
        }
        if (isFinishing()) {
            // 「アプリ起動中のみ」PC から認識される仕様なので、画面を閉じたら待ち受けも終了
            stopService(new Intent(this, TransferService.class));
        }
        super.onDestroy();
    }

    // ------------------------------------------------------------------ 起動手順: 権限 → Bluetooth ON → サービス開始

    private void requestMissingPermissions() {
        if (Build.VERSION.SDK_INT < 23) {
            ensureBluetoothEnabled();
            return;
        }
        List<String> wanted = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31) {
            wanted.add(Manifest.permission.BLUETOOTH_CONNECT);
            wanted.add(Manifest.permission.BLUETOOTH_ADVERTISE);
            wanted.add(Manifest.permission.BLUETOOTH_SCAN);
        }
        if (Build.VERSION.SDK_INT >= 33) wanted.add(Manifest.permission.POST_NOTIFICATIONS);
        if (Build.VERSION.SDK_INT <= 28) wanted.add(Manifest.permission.WRITE_EXTERNAL_STORAGE);
        List<String> missing = new ArrayList<>();
        for (String p : wanted) {
            if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) missing.add(p);
        }
        if (missing.isEmpty()) {
            ensureBluetoothEnabled();
        } else {
            requestPermissions(missing.toArray(new String[0]), REQ_PERMISSIONS);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQ_PERMISSIONS) return;
        // ストレージ権限が拒否されてもアプリ専用領域に保存できるので続行する。
        // Bluetooth 権限（Android 12+）だけは必須。
        if (Build.VERSION.SDK_INT >= 31
                && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            serverStatus.setText(R.string.status_need_permission);
            return;
        }
        ensureBluetoothEnabled();
    }

    private void ensureBluetoothEnabled() {
        if (adapter.isEnabled()) {
            startTransferService();
        } else {
            try {
                startActivityForResult(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE), REQ_ENABLE_BT);
            } catch (SecurityException | ActivityNotFoundException e) {
                serverStatus.setText(R.string.status_bt_off);
            }
        }
    }

    private void startTransferService() {
        Intent i = new Intent(this, TransferService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(i);
        } else {
            startService(i);
        }
        if (!bound) bound = bindService(i, connection, Context.BIND_AUTO_CREATE);
        // 未ペアリングの PC から見つけてもらえるよう、起動時に 1 回だけ検出可能化を求める
        if (!autoDiscoverableDone) {
            autoDiscoverableDone = true;
            if (scanMode() != BluetoothAdapter.SCAN_MODE_CONNECTABLE_DISCOVERABLE) requestDiscoverable();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        switch (requestCode) {
            case REQ_ENABLE_BT:
                if (resultCode == RESULT_OK) {
                    startTransferService();
                } else {
                    serverStatus.setText(R.string.status_bt_off);
                    Toast.makeText(this, R.string.toast_bt_required, Toast.LENGTH_LONG).show();
                }
                break;
            case REQ_DISCOVERABLE:
                // 結果コードは許可された秒数（拒否時は RESULT_CANCELED = 0）
                if (resultCode > 0) {
                    discoverableUntil = System.currentTimeMillis() + resultCode * 1000L;
                    if (!bound && adapter.isEnabled()) startTransferService();
                }
                renderDiscoverable();
                break;
            case REQ_PICK_FILES:
                if (resultCode == RESULT_OK && data != null) {
                    ArrayList<Uri> uris = new ArrayList<>();
                    ClipData clip = data.getClipData();
                    if (clip != null) {
                        for (int i = 0; i < clip.getItemCount(); i++) uris.add(clip.getItemAt(i).getUri());
                    } else if (data.getData() != null) {
                        uris.add(data.getData());
                    }
                    if (!uris.isEmpty()) {
                        pendingUris = uris;
                        chooseDeviceAndSend();
                    }
                }
                break;
            default:
                break;
        }
    }

    // ------------------------------------------------------------------ 操作

    private void requestDiscoverable() {
        Intent i = new Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE)
                .putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, DISCOVERABLE_SECONDS);
        try {
            // Bluetooth がオフなら、この画面で ON にするかも同時に尋ねられる
            startActivityForResult(i, REQ_DISCOVERABLE);
        } catch (SecurityException | ActivityNotFoundException e) {
            Toast.makeText(this, R.string.status_need_permission, Toast.LENGTH_LONG).show();
        }
    }

    private void pickFiles() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*")
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        try {
            startActivityForResult(i, REQ_PICK_FILES);
        } catch (ActivityNotFoundException e) {
            // 一部端末向けのフォールバック
            startActivityForResult(new Intent(Intent.ACTION_GET_CONTENT).addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true), REQ_PICK_FILES);
        }
    }

    /** 共有インテントなら Uri を取り出して true。 */
    @SuppressWarnings("deprecation") // getParcelableExtra(String) は API 33 で非推奨だが minSdk 21 のため使用
    private boolean takeSharedUris(Intent intent) {
        if (intent == null) return false;
        ArrayList<Uri> uris = new ArrayList<>();
        if (Intent.ACTION_SEND.equals(intent.getAction())) {
            Parcelable p = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (p instanceof Uri) uris.add((Uri) p);
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Parcelable> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) {
                for (Parcelable p : list) if (p instanceof Uri) uris.add((Uri) p);
            }
        }
        if (uris.isEmpty()) return false;
        pendingUris = uris;
        // 同じ共有を二重に処理しないよう消費する
        intent.setAction(Intent.ACTION_MAIN);
        return true;
    }

    private void chooseDeviceAndSend() {
        final ArrayList<Uri> uris = pendingUris;
        if (uris == null || service == null) return;
        pendingUris = null;
        final List<BluetoothDevice> devices = pairedDevicesPcFirst();
        if (devices.isEmpty()) {
            new AlertDialog.Builder(this).setMessage(R.string.no_paired_devices)
                    .setPositiveButton(android.R.string.ok, null).show();
            return;
        }
        final SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String last = prefs.getString(PREF_LAST_DEVICE, null);
        String[] labels = new String[devices.size()];
        for (int i = 0; i < devices.size(); i++) {
            BluetoothDevice d = devices.get(i);
            labels[i] = service.deviceLabel(d) + (d.getAddress().equals(last) ? " " + getString(R.string.dialog_last_used) : "");
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.dialog_choose_pc)
                .setItems(labels, (dialog, which) -> {
                    BluetoothDevice d = devices.get(which);
                    prefs.edit().putString(PREF_LAST_DEVICE, d.getAddress()).apply();
                    Toast.makeText(this, R.string.toast_send_hint, Toast.LENGTH_LONG).show();
                    if (service != null) service.sendFiles(d, uris);
                })
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    /** ペアリング済み機器。前回の送信先 → PC → その他 の順。 */
    private List<BluetoothDevice> pairedDevicesPcFirst() {
        List<BluetoothDevice> result = new ArrayList<>();
        Set<BluetoothDevice> bonded;
        try {
            bonded = adapter.getBondedDevices();
        } catch (SecurityException e) {
            return result;
        }
        if (bonded == null) return result;
        final String last = getSharedPreferences(PREFS, MODE_PRIVATE).getString(PREF_LAST_DEVICE, null);
        result.addAll(bonded);
        Collections.sort(result, (a, b) -> Integer.compare(rank(a, last), rank(b, last)));
        return result;
    }

    private static int rank(BluetoothDevice d, String last) {
        if (d.getAddress().equals(last)) return 0;
        BluetoothClass c = d.getBluetoothClass();
        if (c != null && c.getMajorDeviceClass() == BluetoothClass.Device.Major.COMPUTER) return 1;
        return 2;
    }

    private void openEntry(LogEntry e) {
        if (e == null || e.uri == null) return;
        Intent i = new Intent(Intent.ACTION_VIEW).setDataAndType(e.uri, e.mimeType)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(Intent.createChooser(i, null));
        } catch (ActivityNotFoundException ex) {
            Toast.makeText(this, R.string.toast_cannot_open, Toast.LENGTH_SHORT).show();
        }
    }

    private void showHelp() {
        new AlertDialog.Builder(this).setTitle(R.string.help_title).setMessage(R.string.help_body)
                .setPositiveButton(android.R.string.ok, null).show();
    }

    // ------------------------------------------------------------------ 表示

    @Override
    public void onStateChanged() {
        if (service != null && service.isStopping()) {
            // 通知の「停止」から終了された
            finish();
            return;
        }
        if (adapter == null) return;
        String name;
        try {
            name = adapter.getName();
        } catch (SecurityException e) {
            name = "?";
        }
        deviceName.setText(getString(R.string.label_device, name));
        renderDiscoverable();
        if (service == null) return;

        serverStatus.setText(service.serverStatus());
        serverStatus.setTextColor(colorOf(service.isServerListening() ? R.color.primary : R.color.error));
        saveFolder.setText(getString(R.string.label_save_folder, service.saveFolder()));

        TransferService.Progress p = service.progress();
        if (p == null) {
            progressGroup.setVisibility(View.GONE);
        } else {
            progressGroup.setVisibility(View.VISIBLE);
            String label = p.label;
            if (p.total > 0) {
                label += " " + TransferService.formatSize(p.done) + " / " + TransferService.formatSize(p.total);
            } else if (p.done > 0) {
                label += " " + TransferService.formatSize(p.done);
            }
            progressLabel.setText(label);
            progressBar.setIndeterminate(p.total <= 0);
            if (p.total > 0) progressBar.setProgress((int) (p.done * 1000 / p.total));
            findViewById(R.id.buttonCancel).setVisibility(service.isSending() ? View.VISIBLE : View.GONE);
        }
        historyAdapter.setItems(service.logSnapshot());
    }

    private int colorOf(int id) {
        return Build.VERSION.SDK_INT >= 23 ? getResources().getColor(id, getTheme()) : legacyColor(id);
    }

    @SuppressWarnings("deprecation")
    private int legacyColor(int id) {
        return getResources().getColor(id);
    }

    private int scanMode() {
        try {
            return adapter.getScanMode();
        } catch (SecurityException e) {
            return -1;
        }
    }

    private void renderDiscoverable() {
        if (adapter == null || discoverable == null) return;
        int mode = scanMode();
        long remain = (discoverableUntil - System.currentTimeMillis()) / 1000;
        if (mode == BluetoothAdapter.SCAN_MODE_CONNECTABLE_DISCOVERABLE) {
            discoverable.setText(remain > 0 ? getString(R.string.label_discoverable, (int) remain)
                    : getString(R.string.label_discoverable_on));
        } else if (mode == -1) {
            discoverable.setText(R.string.label_discoverable_unknown);
        } else {
            discoverable.setText(R.string.label_discoverable_off);
        }
    }

    private void setButtonsEnabled(boolean enabled) {
        for (int id : new int[] {R.id.buttonDiscoverable, R.id.buttonSend}) {
            Button b = findViewById(id);
            b.setEnabled(enabled);
        }
    }

    /** 履歴リスト。 */
    private static final class HistoryAdapter extends ArrayAdapter<LogEntry> {
        HistoryAdapter(Context context) {
            super(context, android.R.layout.simple_list_item_2, android.R.id.text1);
        }

        void setItems(List<LogEntry> items) {
            setNotifyOnChange(false);
            clear();
            addAll(items);
            notifyDataSetChanged();
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View v = super.getView(position, convertView, parent);
            LogEntry e = getItem(position);
            TextView t1 = v.findViewById(android.R.id.text1);
            TextView t2 = v.findViewById(android.R.id.text2);
            t1.setText(e.text);
            t1.setTextColor(e.error ? 0xFFC62828 : (e.uri != null ? 0xFF1565C0 : 0xFF212121));
            t2.setText(DateFormat.format("MM/dd HH:mm:ss", e.time));
            return v;
        }
    }
}
