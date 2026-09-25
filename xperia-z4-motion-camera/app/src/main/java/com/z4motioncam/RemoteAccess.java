package com.z4motioncam;

import android.content.Context;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URLEncoder;

import javax.net.ssl.SSLSocketFactory;

/**
 * Viewing from outside the home: an HTTPS copy of the web server on its own port (password
 * mandatory, TLS 1.2+, self-signed certificate), a UPnP port mapping on the router, and an
 * optional DuckDNS record that follows the changing global IP. Everything runs on one background
 * thread and wakes up only every few minutes, so it adds no measurable heat.
 */
final class RemoteAccess {
    private static final String TAG = "RemoteAccess";
    static final int MIN_PASSWORD_LENGTH = 8;
    private static final long TICK_MS = 10 * 60_000L;
    private static final long DDNS_FORCE_MS = 6 * 3600_000L;

    private final Context context;
    private final AppSettings settings;
    private final HttpServer.Backend backend;
    private final SSLSocketFactory clientTls;
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
    private volatile String publicV4;
    private volatile String globalV6;
    private volatile String upnpResult;
    private volatile String ddnsResult;
    private volatile Boolean selfReachable;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            refresh();
            handler.postDelayed(this, TICK_MS);
        }
    };

    RemoteAccess(Context context, AppSettings settings, HttpServer.Backend backend, SSLSocketFactory clientTls) {
        this.context = context.getApplicationContext();
        this.settings = settings;
        this.backend = backend;
        this.clientTls = clientTls;
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

    /** Re-checks IP, port mapping and DDNS now (e.g. after the network came back). */
    void refreshSoon() {
        handler.removeCallbacks(tick);
        handler.post(tick);
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
            handler.post(tick);
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
        String local = NetUtil.localIpv4();
        publicV4 = NetUtil.publicIpv4(clientTls);
        globalV6 = NetUtil.globalIpv6();

        if (settings.upnp && local != null) {
            try {
                if (gateway == null || !local.equals(mappedClient)) gateway = Upnp.discover(3000);
                if (gateway == null) {
                    upnpResult = "UPnP対応ルーターが見つかりません（手動でポート転送してください）";
                } else {
                    // Re-adding refreshes the lease and repairs the mapping after a router reboot.
                    Upnp.openPort(gateway, settings.remotePort, local);
                    mapped = true;
                    mappedClient = local;
                    upnpResult = "ルーターの TCP " + settings.remotePort + " 番を開放済み";
                }
            } catch (IOException e) {
                gateway = null;
                upnpResult = "ポート開放に失敗: " + Upnp.describe(e);
            }
        } else if (!settings.upnp) {
            upnpResult = "自動開放オフ（ルーターで手動転送が必要）";
        }

        if (!settings.ddnsDomain.isEmpty() && !settings.ddnsToken.isEmpty()) {
            String ipKey = publicV4 + "/" + globalV6;
            if (!ipKey.equals(ddnsIp) || System.currentTimeMillis() - ddnsAt > DDNS_FORCE_MS) {
                try {
                    String url = "https://www.duckdns.org/update?domains=" + enc(settings.ddnsDomain)
                            + "&token=" + enc(settings.ddnsToken)
                            + (publicV4 != null ? "&ip=" + enc(publicV4) : "")
                            + (globalV6 != null ? "&ipv6=" + enc(globalV6) : "");
                    String r = NetUtil.httpGet(url, 8000, clientTls).trim();
                    if (r.startsWith("OK")) {
                        ddnsIp = ipKey;
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

        selfReachable = publicV4 == null ? null : canConnect(publicV4, settings.remotePort);
    }

    /** Hairpin check: connecting to our own public address works only if the port is open. */
    private static Boolean canConnect(String host, int port) {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), 3000);
            return Boolean.TRUE;
        } catch (IOException e) {
            // Many routers do not support NAT loopback, so a failure here is inconclusive.
            return null;
        } finally {
            try {
                s.close();
            } catch (IOException ignored) {
                // ignore
            }
        }
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (IOException e) {
            return s;
        }
    }

    /** Best URL to open from outside, or null. */
    String url() {
        if (!active) return null;
        if (!settings.ddnsDomain.isEmpty()) return "https://" + settings.ddnsDomain + ".duckdns.org:" + settings.remotePort + "/";
        if (publicV4 != null) return "https://" + publicV4 + ":" + settings.remotePort + "/";
        if (globalV6 != null) return "https://[" + globalV6 + "]:" + settings.remotePort + "/";
        return null;
    }

    String statusLine() {
        if (!settings.remoteEnabled) return null;
        StringBuilder sb = new StringBuilder("外出先: ").append(state);
        String u = url();
        if (u != null) sb.append(' ').append(u);
        if (error != null) sb.append('\n').append(error);
        else if (upnpResult != null) sb.append('\n').append(upnpResult);
        if (fingerprint != null) sb.append("\n証明書 ").append(fingerprint.substring(0, 23)).append('…');
        return sb.toString();
    }

    String statusJson() {
        return "{\"enabled\":" + settings.remoteEnabled + ",\"active\":" + active
                + ",\"state\":" + s(state) + ",\"url\":" + s(url()) + ",\"port\":" + settings.remotePort
                + ",\"error\":" + s(error) + ",\"fingerprint\":" + s(fingerprint)
                + ",\"publicV4\":" + s(publicV4) + ",\"globalV6\":" + s(globalV6)
                + ",\"upnp\":" + s(upnpResult) + ",\"ddns\":" + s(ddnsResult)
                + ",\"selfReachable\":" + (selfReachable == null ? "null" : selfReachable.toString()) + "}";
    }

    private static String s(String v) {
        return v == null ? "null" : HttpServer.jsonString(v);
    }
}
