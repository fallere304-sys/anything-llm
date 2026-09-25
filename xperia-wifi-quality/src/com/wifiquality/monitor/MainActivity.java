package com.wifiquality.monitor;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.net.wifi.ScanResult;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.BaseAdapter;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {

    private static final int SCAN_INTERVAL_SEC = 10;
    private static final int REQ_LOCATION = 1;

    private static final int COLOR_BG = 0xFF121417;
    private static final int COLOR_HEADER = 0xFF1C2026;
    private static final int COLOR_TEXT = 0xFFEDEFF2;
    private static final int COLOR_SUB = 0xFF9AA3AD;
    private static final int COLOR_WARN = 0xFFB03A2E;

    private final Handler handler = new Handler();
    private final SimpleDateFormat timeFormat = new SimpleDateFormat("HH:mm:ss", Locale.JAPAN);

    private WifiManager wifiManager;
    private TextView statusText;
    private TextView warningText;
    private ApAdapter adapter;

    private int secondsLeft = 0;
    private boolean awaitingResults = false;
    private boolean permissionRequested = false;
    private String lastUpdated = "--:--:--";
    private String connectedBssid;
    private List<QualityEstimator.Ap> items = new ArrayList<QualityEstimator.Ap>();

    private final BroadcastReceiver scanReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (awaitingResults) {
                refreshFromResults();
            }
        }
    };

    private final Runnable ticker = new Runnable() {
        @Override
        public void run() {
            secondsLeft--;
            if (secondsLeft <= 0) {
                performScan();
                secondsLeft = SCAN_INTERVAL_SEC;
            }
            updateStatus();
            handler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        wifiManager = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildLayout());
    }

    private View buildLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(COLOR_BG);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setBackgroundColor(COLOR_HEADER);
        header.setPadding(dp(12), dp(8), dp(12), dp(8));

        statusText = new TextView(this);
        statusText.setTextColor(COLOR_TEXT);
        statusText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        header.addView(statusText);

        TextView legend = new TextView(this);
        legend.setTextColor(COLOR_SUB);
        legend.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        legend.setText("電波の強い順 / 推定品質 = 信号係数 × 混線係数（至近距離・混線なし = 100%）\n"
                + "混線係数 ≈ 1 − ch使用率（AP実測値。無ければビーコン計算値と実測統計の典型値）");
        header.addView(legend);
        root.addView(header);

        warningText = new TextView(this);
        warningText.setBackgroundColor(COLOR_WARN);
        warningText.setTextColor(0xFFFFFFFF);
        warningText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        warningText.setPadding(dp(12), dp(10), dp(12), dp(10));
        warningText.setVisibility(View.GONE);
        root.addView(warningText);

        ListView list = new ListView(this);
        list.setDivider(new android.graphics.drawable.ColorDrawable(0xFF2A2F36));
        list.setDividerHeight(dp(1));
        adapter = new ApAdapter();
        list.setAdapter(adapter);
        root.addView(list, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    @Override
    protected void onResume() {
        super.onResume();
        registerReceiver(scanReceiver, new IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION));
        secondsLeft = 0; // 直ちに 1 回目のスキャン
        handler.removeCallbacks(ticker);
        handler.post(ticker);
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(ticker);
        unregisterReceiver(scanReceiver);
    }

    // ---- スキャン ----

    private void performScan() {
        if (!checkPreconditions()) {
            return;
        }
        if (awaitingResults) {
            // 前回のスキャン完了通知が来なかった場合はキャッシュ済みの結果で更新
            refreshFromResults();
        }
        boolean started = false;
        try {
            started = wifiManager.startScan();
        } catch (SecurityException e) {
            // 権限が無い場合は checkPreconditions で案内済み
        }
        awaitingResults = true;
        if (!started) {
            refreshFromResults();
        }
    }

    private void refreshFromResults() {
        awaitingResults = false;
        List<ScanResult> results;
        try {
            results = wifiManager.getScanResults();
        } catch (SecurityException e) {
            results = null;
        }
        List<QualityEstimator.Ap> aps = new ArrayList<QualityEstimator.Ap>();
        if (results != null) {
            for (ScanResult r : results) {
                aps.add(toAp(r));
            }
        }
        WifiInfo info = wifiManager.getConnectionInfo();
        connectedBssid = info != null ? info.getBSSID() : null;
        items = QualityEstimator.evaluate(aps);
        lastUpdated = timeFormat.format(new Date());
        adapter.notifyDataSetChanged();
        updateStatus();
    }

    private static QualityEstimator.Ap toAp(ScanResult r) {
        int width = 20;
        int center0 = 0;
        if (Build.VERSION.SDK_INT >= 23) {
            width = widthFromConstant(r.channelWidth);
            center0 = r.centerFreq0;
        }
        QualityEstimator.Ap ap = new QualityEstimator.Ap(r.SSID, r.BSSID, r.frequency, center0, width, r.level);
        readInformationElements(r, ap);
        return ap;
    }

    /**
     * ScanResult の非公開フィールド informationElements をリフレクションで読む。
     * Android 7.x では wpa_supplicant から受け取った全情報要素が入る（5.x / 6.x は null）。
     * Android 8 以前は非公開 API の制限が無いため読み取れる。
     */
    private static void readInformationElements(ScanResult r, QualityEstimator.Ap ap) {
        try {
            Object arr = ScanResult.class.getField("informationElements").get(r);
            if (arr == null) return;
            int n = java.lang.reflect.Array.getLength(arr);
            if (n == 0) return;
            int total = 0;
            double basic = -1;
            for (int i = 0; i < n; i++) {
                Object ie = java.lang.reflect.Array.get(arr, i);
                int id = ie.getClass().getField("id").getInt(ie);
                byte[] body = (byte[]) ie.getClass().getField("bytes").get(ie);
                int len = body != null ? body.length : 0;
                total += 2 + len;
                if (id == 11) {
                    ap.bssLoadUtilization = QualityEstimator.parseBssLoadUtilization(body);
                } else if (id == 1 || id == 50) {
                    basic = QualityEstimator.lowestBasicRate(body, basic);
                }
            }
            ap.beaconBytes = QualityEstimator.beaconLengthFromIeBytes(total);
            ap.basicRateMbps = basic;
        } catch (Throwable t) {
            // 取得できない端末では推定値にフォールバック
        }
    }

    private static int widthFromConstant(int c) {
        switch (c) {
            case 1: return 40;   // CHANNEL_WIDTH_40MHZ
            case 2: return 80;   // CHANNEL_WIDTH_80MHZ
            case 3: return 160;  // CHANNEL_WIDTH_160MHZ
            case 4: return 160;  // CHANNEL_WIDTH_80MHZ_PLUS_MHZ
            default: return 20;
        }
    }

    // ---- 前提条件（Wi-Fi / 権限 / 位置情報） ----

    private boolean checkPreconditions() {
        if (Build.VERSION.SDK_INT >= 23 && !hasLocationPermission()) {
            showWarning("スキャン結果の取得には位置情報の権限が必要です（タップで許可）",
                    new View.OnClickListener() {
                        @Override
                        public void onClick(View v) {
                            requestLocationPermission();
                        }
                    });
            if (!permissionRequested) {
                permissionRequested = true;
                requestLocationPermission();
            }
            return false;
        }
        if (Build.VERSION.SDK_INT >= 23 && !isLocationEnabled()) {
            showWarning("位置情報(GPS)がオフのためスキャンできません（タップで設定を開く）",
                    new View.OnClickListener() {
                        @Override
                        public void onClick(View v) {
                            startActivity(new Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS));
                        }
                    });
            return false;
        }
        if (!wifiManager.isWifiEnabled() && !wifiManager.isScanAlwaysAvailable()) {
            showWarning("Wi-Fiがオフです（タップでオンにする）", new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    wifiManager.setWifiEnabled(true);
                    secondsLeft = 3;
                }
            });
            return false;
        }
        warningText.setVisibility(View.GONE);
        return true;
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void requestLocationPermission() {
        requestPermissions(new String[] {
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode == REQ_LOCATION) {
            secondsLeft = 0;
        }
    }

    private boolean isLocationEnabled() {
        try {
            int mode = Settings.Secure.getInt(getContentResolver(), Settings.Secure.LOCATION_MODE);
            return mode != Settings.Secure.LOCATION_MODE_OFF;
        } catch (Settings.SettingNotFoundException e) {
            return true;
        }
    }

    private void showWarning(String message, View.OnClickListener onClick) {
        warningText.setText(message);
        warningText.setOnClickListener(onClick);
        warningText.setVisibility(View.VISIBLE);
    }

    private void updateStatus() {
        statusText.setText("最終更新 " + lastUpdated
                + "   検出 " + items.size() + " 件"
                + "   次回スキャンまで " + Math.max(secondsLeft, 0) + " 秒");
    }

    // ---- 一覧表示 ----

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private static int colorForQuality(int q) {
        if (q >= 70) return 0xFF2ECC71;
        if (q >= 40) return 0xFFF1C40F;
        if (q >= 20) return 0xFFE67E22;
        return 0xFFE74C3C;
    }

    private static final class Holder {
        TextView ssid;
        TextView detail;
        TextView quality;
        SignalBarView bar;
        TextView interference;
    }

    private final class ApAdapter extends BaseAdapter {
        @Override
        public int getCount() {
            return items.size();
        }

        @Override
        public Object getItem(int position) {
            return items.get(position);
        }

        @Override
        public long getItemId(int position) {
            return position;
        }

        @Override
        public boolean isEnabled(int position) {
            return false;
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            Holder h;
            if (convertView == null) {
                h = new Holder();
                convertView = createRow(h);
                convertView.setTag(h);
            } else {
                h = (Holder) convertView.getTag();
            }
            bind(h, items.get(position));
            return convertView;
        }

        private View createRow(Holder h) {
            LinearLayout row = new LinearLayout(MainActivity.this);
            row.setOrientation(LinearLayout.VERTICAL);
            row.setPadding(dp(12), dp(8), dp(12), dp(8));

            LinearLayout top = new LinearLayout(MainActivity.this);
            top.setOrientation(LinearLayout.HORIZONTAL);
            top.setGravity(Gravity.CENTER_VERTICAL);

            LinearLayout left = new LinearLayout(MainActivity.this);
            left.setOrientation(LinearLayout.VERTICAL);
            h.ssid = new TextView(MainActivity.this);
            h.ssid.setTextColor(COLOR_TEXT);
            h.ssid.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
            h.ssid.setTypeface(Typeface.DEFAULT_BOLD);
            h.ssid.setSingleLine(true);
            left.addView(h.ssid);
            h.detail = new TextView(MainActivity.this);
            h.detail.setTextColor(COLOR_SUB);
            h.detail.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            h.detail.setSingleLine(true);
            left.addView(h.detail);
            top.addView(left, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

            LinearLayout right = new LinearLayout(MainActivity.this);
            right.setOrientation(LinearLayout.VERTICAL);
            right.setGravity(Gravity.END);
            h.quality = new TextView(MainActivity.this);
            h.quality.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
            h.quality.setTypeface(Typeface.DEFAULT_BOLD);
            h.quality.setGravity(Gravity.END);
            right.addView(h.quality);
            TextView qLabel = new TextView(MainActivity.this);
            qLabel.setText("推定品質");
            qLabel.setTextColor(COLOR_SUB);
            qLabel.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
            qLabel.setGravity(Gravity.END);
            right.addView(qLabel);
            LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            rlp.leftMargin = dp(8);
            top.addView(right, rlp);
            row.addView(top);

            h.bar = new SignalBarView(MainActivity.this);
            LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            blp.topMargin = dp(4);
            blp.bottomMargin = dp(4);
            row.addView(h.bar, blp);

            h.interference = new TextView(MainActivity.this);
            h.interference.setTextColor(COLOR_SUB);
            h.interference.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            row.addView(h.interference);
            return row;
        }

        private void bind(Holder h, QualityEstimator.Ap ap) {
            String name = (ap.ssid == null || ap.ssid.length() == 0) ? "(非公開SSID)" : ap.ssid;
            boolean connected = ap.bssid != null && ap.bssid.equalsIgnoreCase(connectedBssid);
            h.ssid.setText(connected ? "★ " + name + "（接続中）" : name);

            String width = Build.VERSION.SDK_INT >= 23 ? ap.widthMhz + "MHz" : "幅不明";
            h.detail.setText("ch " + ap.channel + "  " + ap.band + " / " + width + "   " + ap.bssid);

            h.quality.setText(ap.qualityPercent + "%");
            h.quality.setTextColor(colorForQuality(ap.qualityPercent));

            h.bar.setRssi(ap.rssi);

            String busy = ap.measured
                    ? String.format(Locale.JAPAN, "ch使用率 %d%%(AP実測)", Math.round(ap.primaryBusy * 100))
                    : String.format(Locale.JAPAN, "ch使用率 %d%%(推定)", Math.round(ap.primaryBusy * 100));
            String secondary = ap.secondaryCount > 0 ? " / 副ch " + ap.secondaryCount : "";
            h.interference.setText(String.format(Locale.JAPAN,
                    "%s（同ch %d / 重複 %d%s） %s\n信号 %d%% × 混線 %d%%",
                    QualityEstimator.congestionLabel(ap.interferenceFactor),
                    ap.coChannelCount, ap.overlapCount, secondary, busy,
                    Math.round(ap.signalFactor * 100), Math.round(ap.interferenceFactor * 100)));
        }
    }
}
