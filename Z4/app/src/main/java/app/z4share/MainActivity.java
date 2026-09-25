package app.z4share;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Parcelable;
import android.text.format.Formatter;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * QR コードを表示して iPhone の Safari を Z4 のサーバーへ案内する画面。
 * 画面の部品は res を使わずコードで組み立てている。
 */
public class MainActivity extends Activity implements ShareService.UiListener {

    private static final int REQ_PICK = 1;
    private static final int REQ_PERMISSIONS = 2;
    private static final long REFRESH_MS = 4000;

    private final Handler handler = new Handler();
    private final List<Uri> pendingShares = new ArrayList<>();
    private ShareService service;
    private boolean bound;

    private TextView statusView;
    private ImageView qrView;
    private TextView urlView;
    private LinearLayout outboxList;
    private LinearLayout receivedList;
    private TextView receivedHeader;
    private Button openDownloads;

    private List<NetUtil.LanAddress> addresses = new ArrayList<>();
    private int addressIndex;
    private String shownUrl;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = ((ShareService.LocalBinder) binder).getService();
            service.setUiListener(MainActivity.this);
            // 権限ダイアログの結果が bind 前に来た場合もあるので、受信先はここでも選び直す
            service.refreshInboxDir();
            flushPendingShares();
            refreshAll();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            service = null;
        }
    };

    private final Runnable periodicRefresh = new Runnable() {
        @Override
        public void run() {
            refreshAll();
            handler.postDelayed(this, REFRESH_MS);
        }
    };

    // ---------------------------------------------------------------- lifecycle

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildUi());
        requestNeededPermissions();
        collectSharedUris(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        collectSharedUris(intent);
        flushPendingShares();
    }

    @Override
    protected void onStart() {
        super.onStart();
        // bind だけだと裏に回った時に止まるので、毎回 start もしておく (二重起動にはならない)。
        // 画面が前面にある時なので Android 8 以降でも startService で起動でき、
        // サービス自身が onCreate で startForeground する。
        startService(new Intent(this, ShareService.class));
        bound = bindService(new Intent(this, ShareService.class), connection, Context.BIND_AUTO_CREATE);
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(periodicRefresh);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(periodicRefresh);
        super.onPause();
    }

    @Override
    protected void onStop() {
        if (service != null) {
            service.setUiListener(null);
        }
        if (bound) {
            unbindService(connection);
            bound = false;
        }
        service = null;
        super.onStop();
    }

    /** 戻るキーではサーバーを止めずに裏へ回す。止めるのは「停止して終了」か通知の「停止」。 */
    @Override
    public void onBackPressed() {
        moveTaskToBack(true);
    }

    @Override
    public void onFilesChanged() {
        refreshAll();
    }

    @Override
    public void onServiceStopped() {
        finish();
    }

    // ---------------------------------------------------------------- permissions

    private void requestNeededPermissions() {
        List<String> want = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 23 && Build.VERSION.SDK_INT <= 28
                && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            want.add(Manifest.permission.WRITE_EXTERNAL_STORAGE);
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            want.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!want.isEmpty()) {
            requestPermissions(want.toArray(new String[0]), REQ_PERMISSIONS);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode == REQ_PERMISSIONS && service != null) {
            service.refreshInboxDir();
        }
    }

    // ---------------------------------------------------------------- Z4 -> iPhone

    private void pickFiles() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*")
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        try {
            startActivityForResult(i, REQ_PICK);
        } catch (ActivityNotFoundException e) {
            startActivityForResult(new Intent(Intent.ACTION_GET_CONTENT)
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("*/*")
                    .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true), REQ_PICK);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PICK || resultCode != RESULT_OK || data == null) {
            return;
        }
        ClipData clip = data.getClipData();
        if (clip != null) {
            for (int i = 0; i < clip.getItemCount(); i++) {
                Uri u = clip.getItemAt(i).getUri();
                if (u != null) {
                    pendingShares.add(u);
                }
            }
        } else if (data.getData() != null) {
            pendingShares.add(data.getData());
        }
        flushPendingShares();
    }

    /** ギャラリー等の「共有」メニューから Z4 Share が選ばれた時の URI を拾う。 */
    private void collectSharedUris(Intent intent) {
        if (intent == null) {
            return;
        }
        String action = intent.getAction();
        if (Intent.ACTION_SEND.equals(action)) {
            Parcelable p = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (p instanceof Uri) {
                pendingShares.add((Uri) p);
            }
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(action)) {
            ArrayList<Parcelable> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) {
                for (Parcelable p : list) {
                    if (p instanceof Uri) {
                        pendingShares.add((Uri) p);
                    }
                }
            }
        }
        setIntent(new Intent(this, MainActivity.class));
    }

    private void flushPendingShares() {
        if (service == null || pendingShares.isEmpty()) {
            return;
        }
        final int count = pendingShares.size();
        Toast.makeText(this, count + " 件を準備しています…", Toast.LENGTH_SHORT).show();
        service.addToOutbox(new ArrayList<>(pendingShares), new Runnable() {
            @Override
            public void run() {
                Toast.makeText(MainActivity.this,
                        "iPhone の画面に " + count + " 件表示されました", Toast.LENGTH_SHORT).show();
            }
        });
        pendingShares.clear();
    }

    // ---------------------------------------------------------------- refresh

    private void refreshAll() {
        refreshAddress();
        if (service == null) {
            return;
        }
        fillList(outboxList, service.getOutboxFiles(), true,
                "まだありません。「ファイルを追加」か、ギャラリー等の「共有」→「Z4 Share」で追加できます。");
        fillList(receivedList, service.getReceivedFiles(), false, "まだありません。");
        String where = service.isInboxPublicDownloads()
                ? "内部ストレージ/Download/Z4Share"
                : service.getInboxDir().getAbsolutePath();
        receivedHeader.setText("受信したファイル（保存先: " + where + "）");
        openDownloads.setVisibility(service.isInboxPublicDownloads() ? View.VISIBLE : View.GONE);
    }

    private void refreshAddress() {
        addresses = NetUtil.lanAddresses();
        if (service == null) {
            statusView.setText("起動中…");
            return;
        }
        if (service.getPort() < 0) {
            statusView.setText("サーバーを起動できませんでした: " + service.getStartError());
            showQr(null);
            return;
        }
        if (addresses.isEmpty()) {
            statusView.setText("Wi-Fi に接続されていません。\nWi-Fi に接続するか、Z4 のテザリング（Wi-Fi アクセスポイント）をオンにしてください。");
            showQr(null);
            return;
        }
        if (addressIndex >= addresses.size()) {
            addressIndex = 0;
        }
        NetUtil.LanAddress a = addresses.get(addressIndex);
        statusView.setText(a.isHotspot()
                ? "iPhone を Z4 のテザリングに接続し、カメラでこの QR コードを読み取ってください。"
                : "iPhone を Z4 と同じ Wi-Fi に接続し、カメラでこの QR コードを読み取ってください。");
        showQr("http://" + a.ip + ":" + service.getPort() + "/?k=" + service.getToken());
    }

    private void showQr(String url) {
        if (url == null) {
            shownUrl = null;
            qrView.setVisibility(View.GONE);
            urlView.setText("");
            return;
        }
        if (url.equals(shownUrl)) {
            return;
        }
        shownUrl = url;
        int size = Math.min(getResources().getDisplayMetrics().widthPixels - dp(64), dp(300));
        Bitmap bmp = QrUtil.encode(url, size);
        qrView.setImageBitmap(bmp);
        qrView.setVisibility(bmp != null ? View.VISIBLE : View.GONE);
        String hint = addresses.size() > 1 ? "\n（タップで別のネットワークに切替: "
                + (addressIndex + 1) + "/" + addresses.size() + "）" : "";
        urlView.setText(url.substring(0, url.indexOf("/?k=") + 1) + hint);
    }

    private void fillList(LinearLayout list, List<File> files, boolean removable, String emptyText) {
        list.removeAllViews();
        if (files.isEmpty()) {
            list.addView(text(emptyText, 14, Color.GRAY));
            return;
        }
        for (final File f : files) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(0, dp(6), 0, dp(6));
            TextView label = text(f.getName() + "\n" + Formatter.formatFileSize(this, f.length()), 15, 0);
            row.addView(label, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
            if (removable) {
                Button del = new Button(this);
                del.setText("取消");
                del.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        if (service != null) {
                            service.removeFromOutbox(f);
                        }
                    }
                });
                row.addView(del);
            }
            list.addView(row);
        }
    }

    // ---------------------------------------------------------------- UI construction

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(16), dp(16), dp(24));

        TextView title = text("Z4 Share", 24, 0);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        statusView = text("起動中…", 16, 0);
        statusView.setPadding(0, dp(8), 0, dp(8));
        root.addView(statusView);

        qrView = new ImageView(this);
        qrView.setBackgroundColor(Color.WHITE);
        qrView.setAdjustViewBounds(true);
        qrView.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (addresses.size() > 1) {
                    addressIndex = (addressIndex + 1) % addresses.size();
                    refreshAddress();
                }
            }
        });
        LinearLayout.LayoutParams qrLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        qrLp.gravity = Gravity.CENTER_HORIZONTAL;
        root.addView(qrView, qrLp);

        urlView = text("", 14, Color.GRAY);
        urlView.setGravity(Gravity.CENTER_HORIZONTAL);
        urlView.setTypeface(Typeface.MONOSPACE);
        urlView.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                qrView.performClick();
            }
        });
        root.addView(urlView);

        root.addView(header("iPhone に送るファイル"));
        Button add = new Button(this);
        add.setText("ファイルを追加");
        add.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                pickFiles();
            }
        });
        root.addView(add);
        outboxList = new LinearLayout(this);
        outboxList.setOrientation(LinearLayout.VERTICAL);
        root.addView(outboxList);

        receivedHeader = header("受信したファイル");
        root.addView(receivedHeader);
        openDownloads = new Button(this);
        openDownloads.setText("ダウンロードを開く");
        openDownloads.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                try {
                    startActivity(new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS));
                } catch (ActivityNotFoundException e) {
                    Toast.makeText(MainActivity.this, "ファイルアプリが見つかりません", Toast.LENGTH_SHORT).show();
                }
            }
        });
        root.addView(openDownloads);
        receivedList = new LinearLayout(this);
        receivedList.setOrientation(LinearLayout.VERTICAL);
        root.addView(receivedList);

        Button stop = new Button(this);
        stop.setText("停止して終了");
        stop.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                stopService(new Intent(MainActivity.this, ShareService.class));
                finish();
            }
        });
        LinearLayout.LayoutParams stopLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        stopLp.topMargin = dp(24);
        root.addView(stop, stopLp);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        return scroll;
    }

    private TextView header(String s) {
        TextView t = text(s, 18, 0);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(20), 0, dp(6));
        return t;
    }

    private TextView text(String s, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        if (color != 0) {
            t.setTextColor(color);
        }
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
