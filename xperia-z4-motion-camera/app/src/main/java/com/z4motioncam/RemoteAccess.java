package com.z4motioncam;

import android.content.Context;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.net.URLEncoder;

import javax.net.ssl.SSLSocketFactory;

/**
 * Viewing from outside the home: an HTTPS copy of the web server on its own port (password
 * mandatory, TLS 1.2+, self-signed certificate) plus, optionally, a UPnP port mapping on the router
 * and a DuckDNS record. The router's own WAN address (via UPnP) is the global IP, so no outside
 * IP-lookup service is contacted. Background work runs every 30 minutes at most.
 */
final class RemoteAccess {
    private static final String TAG = "RemoteAccess";
    static final int MIN_PASSWORD_LENGTH = 8;
    private static final long TICK_MS = 30 * 60_000L;
    /** DuckDNS is refreshed at least this often even when the address looks unchanged. */
    private static final long DDNS_FORCE_MS = 12 * 3600_000L;

    private final Context context;
    private final AppSettings settings;
    private final HttpServer.Backend backend;
    private final SSLSocketFactory ddnsTls;
    private final HandlerThread thread = new HandlerThread("remote");
    private final Handler handler;

    private HttpServer https;
    private Upnp.Gateway gateway;
    private boolean mapped;
    private String mappedClient;
    private String ddnsIp;
    private long ddnsAt;

    // Status, read from other threads.
    private volatile String state = "無効";
    private volatile boolean active;
    private volatile String error;
    private volatile String fingerprint;
    /** Global IPv4 as reported by the router, or null. */
    private volatile String wanIp;
    private volatile String upnpResult;
    private volatile String ddnsResult;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            refresh();
            handler.postDelayed(this, TICK_MS);
        }
    };

    RemoteAccess(Context context, AppSettings settings, HttpServer.Backend backend, SSLSocketFactory ddnsTls) {
        this.context = context.getApplicationContext();
        this.settings = settings;
        this.backend = backend;
        this.ddnsTls = ddnsTls;
        thread.start();
        handler = new Handler(thread.getLooper());
    }

    void start() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                open();
            }
        });
    }

    void shutdown() {
        handler.removeCallbacksAndMessages(null);
        handler.post(new Runnable() {
            @Override
            public void run() {
                close();
                thread.quit();
            }
        });
        try {
            thread.join(8_000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** Re-checks the port mapping and DDNS now (e.g. after Wi-Fi reconnected). */
    void refreshSoon() {
        if (!active || !hasBackgroundWork()) return;
        handler.removeCallbacks(tick);
        handler.post(tick);
    }

    private boolean hasBackgroundWork() {
        return settings.upnp || settings.ddnsConfigured();
    }

    private void open() {
        if (!settings.remoteEnabled) {
            state = "無効";
            return;
        }
        if (settings.password.length() < MIN_PASSWORD_LENGTH) {
            state = "停止中";
            error = "外出先からの視聴には" + MIN_PASSWORD_LENGTH + "文字以上のパスワードが必要です";
            return;
        }
        if (settings.remotePort == settings.port) {
            state = "停止中";
            error = "外出先用ポートはLAN用ポートと別の番号にしてください";
            return;
        }
        try {
            state = "準備中";
            TlsIdentity id = TlsIdentity.loadOrCreate(new File(context.getFilesDir(), "tls.keystore"));
            fingerprint = id.fingerprint();
            https = new HttpServer(settings.remotePort, backend, id.serverSocketFactory(), false, true);
            https.start();
            active = true;
            state = "公開中";
            error = null;
            if (hasBackgroundWork()) handler.post(tick);
        } catch (Exception e) {
            Log.e(TAG, "https start failed", e);
            state = "停止中";
            error = "HTTPSサーバーを起動できません: " + e.getMessage();
            https = null;
        }
    }

    private void close() {
        if (mapped && gateway != null) {
            try {
                Upnp.deletePortMapping(gateway, settings.remotePort);
            } catch (IOException e) {
                Log.w(TAG, "unmap failed", e);
            }
            mapped = false;
        }
        if (https != null) https.stop();
        https = null;
        active = false;
    }

    private void refresh() {
        if (settings.upnp) {
            String local = NetUtil.localIpv4();
            if (local == null) {
                upnpResult = "Wi-Fiに接続されていません";
            } else {
                try {
                    if (gateway == null || !local.equals(mappedClient)) gateway = Upnp.discover(3000);
                    if (gateway == null) {
                        upnpResult = "UPnP対応ルーターが見つかりません（ルーターで手動転送してください）";
                    } else {
                        // Re-adding refreshes the lease and repairs the mapping after a router reboot.
                        Upnp.openPort(gateway, settings.remotePort, local);
                        mapped = true;
                        mappedClient = local;
                        upnpResult = "ルーターの TCP " + settings.remotePort + " 番を開放済み";
                        String ip = Upnp.externalIp(gateway);
                        wanIp = NetUtil.isPublicV4(ip) ? ip : null;
                    }
                } catch (IOException e) {
                    gateway = null;
                    upnpResult = "ポート開放に失敗: " + Upnp.describe(e);
                }
            }
        } else {
            upnpResult = "自動開放オフ（ルーターで手動転送）";
        }

        if (settings.ddnsConfigured()) {
            String key = String.valueOf(wanIp);
            // Unknown address: update every tick; known: only when it changed (or every 12 h).
            if (wanIp == null || !key.equals(ddnsIp) || System.currentTimeMillis() - ddnsAt > DDNS_FORCE_MS) {
                try {
                    // An empty ip= makes DuckDNS use the address this request comes from.
                    String url = "https://www.duckdns.org/update?domains=" + enc(settings.ddnsDomain)
                            + "&token=" + enc(settings.ddnsToken) + "&ip=" + (wanIp == null ? "" : wanIp);
                    String r = NetUtil.httpGet(url, 8000, ddnsTls).trim();
                    if (r.startsWith("OK")) {
                        ddnsIp = key;
                        ddnsAt = System.currentTimeMillis();
                        ddnsResult = "DuckDNS 更新済み";
                    } else {
                        ddnsResult = "DuckDNS 更新失敗（ドメイン名またはトークンを確認）";
                    }
                } catch (IOException e) {
                    ddnsResult = "DuckDNS に接続できません: " + e.getMessage();
                }
            }
        } else {
            ddnsResult = null;
        }
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (IOException e) {
            return s;
        }
    }

    /** Host for the outside URL: DuckDNS name, then the configured address, then the router's WAN IP. */
    private String host() {
        if (settings.ddnsConfigured()) return settings.ddnsDomain + ".duckdns.org";
        if (!settings.externalHost.isEmpty()) return settings.externalHost;
        return wanIp;
    }

    String url() {
        String h = host();
        return active && h != null ? "https://" + h + ":" + settings.remotePort + "/" : null;
    }

    /** Warns when the router now reports a different global IP than the configured one. */
    private String ipWarning() {
        String w = wanIp;
        if (w == null || settings.externalHost.isEmpty() || settings.ddnsConfigured()
                || w.equals(settings.externalHost) || NetUtil.parseV4(settings.externalHost) == null) {
            return null;
        }
        return "グローバルIPが " + w + " に変わっています（設定の外部アドレスを更新してください）";
    }

    String statusLine() {
        if (!settings.remoteEnabled) return null;
        StringBuilder sb = new StringBuilder("外出先: ").append(state);
        String u = url();
        if (u != null) sb.append(' ').append(u);
        String warn = ipWarning();
        if (error != null) sb.append('\n').append(error);
        else if (warn != null) sb.append('\n').append(warn);
        else if (upnpResult != null) sb.append('\n').append(upnpResult);
        if (fingerprint != null) sb.append("\n証明書 ").append(fingerprint.substring(0, 23)).append('…');
        return sb.toString();
    }

    String statusJson() {
        return "{\"enabled\":" + settings.remoteEnabled + ",\"active\":" + active
                + ",\"state\":" + s(state) + ",\"url\":" + s(url()) + ",\"port\":" + settings.remotePort
                + ",\"error\":" + s(error != null ? error : ipWarning()) + ",\"fingerprint\":" + s(fingerprint)
                + ",\"wanIp\":" + s(wanIp) + ",\"upnp\":" + s(upnpResult) + ",\"ddns\":" + s(ddnsResult) + "}";
    }

    private static String s(String v) {
        return v == null ? "null" : HttpServer.jsonString(v);
    }
}
