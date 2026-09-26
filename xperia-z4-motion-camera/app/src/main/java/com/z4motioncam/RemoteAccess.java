package com.z4motioncam;

import android.content.Context;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;

import javax.net.ssl.SSLSocketFactory;

/**
 * Viewing from outside the home: an HTTPS copy of the web server on its own port (password
 * mandatory, TLS 1.2+) plus, optionally, a UPnP port mapping on the router, a DuckDNS record and a
 * Let's Encrypt certificate for the DuckDNS name (otherwise the certificate is self-signed). The
 * router's own WAN address (via UPnP) is the global IP, so no outside IP-lookup service is
 * contacted. Background work runs every 30 minutes at most.
 */
final class RemoteAccess {
    private static final String TAG = "RemoteAccess";
    static final int MIN_PASSWORD_LENGTH = 8;
    private static final long TICK_MS = 30 * 60_000L;
    /** DuckDNS is refreshed at least this often even when the address looks unchanged. */
    private static final long DDNS_FORCE_MS = 12 * 3600_000L;
    /** Wait after setting the TXT record before Let's Encrypt looks it up. */
    private static final long TXT_PROPAGATION_MS = 60_000L;
    /** Failed certificate attempts back off from 1 h to 24 h (Let's Encrypt limits failures per hour). */
    static final long CERT_RETRY_MIN_MS = 3600_000L;
    static final long CERT_RETRY_MAX_MS = 24 * 3600_000L;

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
    private volatile boolean servingIssued;
    private volatile boolean closing;
    private long certNextTry;
    private long certBackoff = CERT_RETRY_MIN_MS;

    // Status, read from other threads.
    private volatile String state = "無効";
    private volatile boolean active;
    private volatile String error;
    private volatile String fingerprint;
    /** Global IPv4 as reported by the router, or null. */
    private volatile String wanIp;
    private volatile String upnpResult;
    private volatile String ddnsResult;
    /** Which certificate is served and, for Let's Encrypt, how getting it went. */
    private volatile String certResult;

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
        closing = true;
        handler.removeCallbacksAndMessages(null);
        // A certificate request can take a minute or two: cut it short (its waits are interruptible)
        // so the port is free before the next RemoteAccess opens it.
        thread.interrupt();
        handler.post(new Runnable() {
            @Override
            public void run() {
                Thread.interrupted(); // clear it: the UPnP unmapping below must not be cut short
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

    /** A Let's Encrypt certificate is wanted: switched on, and a DuckDNS name to certify. */
    private boolean wantsIssued() {
        return settings.acme && settings.ddnsConfigured();
    }

    private String ddnsHost() {
        return settings.ddnsDomain + ".duckdns.org";
    }

    private File issuedFile() {
        return new File(context.getFilesDir(), "tls-issued.keystore");
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
        state = "準備中";
        if (startServer() && hasBackgroundWork()) handler.post(tick);
    }

    /** Starts the HTTPS server with the Let's Encrypt certificate when there is a usable one. */
    private boolean startServer() {
        try {
            TlsIdentity id = null;
            if (wantsIssued()) {
                TlsIdentity issued = TlsIdentity.loadIssued(issuedFile());
                if (issued != null && issued.covers(ddnsHost(), System.currentTimeMillis())) id = issued;
            }
            servingIssued = id != null;
            if (id == null) id = TlsIdentity.loadOrCreate(new File(context.getFilesDir(), "tls.keystore"), certHosts());
            if (servingIssued) {
                if (certResult == null || !certResult.contains("失敗")) certResult = id.describeIssued();
            } else if (!wantsIssued()) {
                certResult = "自己署名";
            } else if (certResult == null) {
                certResult = "自己署名（Let's Encrypt の証明書を取得待ち）";
            }
            fingerprint = id.fingerprint();
            https = new HttpServer(settings.remotePort, backend, id.serverSocketFactory(), false, true);
            https.start();
            active = true;
            state = "公開中";
            error = null;
            return true;
        } catch (Exception e) {
            Log.e(TAG, "https start failed", e);
            state = "停止中";
            error = "HTTPSサーバーを起動できません: " + e.getMessage();
            https = null;
            active = false;
            return false;
        }
    }

    /** Swaps in a new certificate: a restart of about a second, once every two months or so. */
    private void restartServer() {
        if (https == null || closing) return;
        https.stop();
        https = null;
        active = false;
        startServer();
    }

    /**
     * Addresses the certificate must name: the outside address, the DuckDNS name and the LAN IP
     * (so https://LAN-IP:port/ can be tested at home). A change produces a new certificate.
     */
    private List<String> certHosts() {
        List<String> hosts = new ArrayList<>();
        if (!settings.externalHost.isEmpty()) hosts.add(settings.externalHost);
        if (settings.ddnsConfigured()) hosts.add(settings.ddnsDomain + ".duckdns.org");
        String lan = NetUtil.localIpv4();
        if (lan != null && !hosts.contains(lan)) hosts.add(lan);
        return hosts;
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
                    // No address makes DuckDNS use the one this request comes from.
                    if (duckDns().updateIp(wanIp)) {
                        ddnsIp = key;
                        ddnsAt = System.currentTimeMillis();
                        ddnsResult = "DuckDNS 更新済み";
                    } else {
                        ddnsResult = "DuckDNS 更新失敗（ドメイン名またはトークンを確認）";
                    }
                } catch (Acme.AcmeException e) {
                    ddnsResult = e.userMessage;
                } catch (IOException e) {
                    ddnsResult = "DuckDNS に接続できません";
                }
            }
        } else {
            ddnsResult = null;
        }

        if (wantsIssued()) renewCertificate();
    }

    private DuckDns duckDns() {
        return new DuckDns(DuckDns.UPDATE_URL, settings.ddnsDomain, settings.ddnsToken, ddnsTls);
    }

    /** Gets a Let's Encrypt certificate when there is none for the DuckDNS name or it is due for renewal. */
    private void renewCertificate() {
        long now = System.currentTimeMillis();
        TlsIdentity current = TlsIdentity.loadIssued(issuedFile());
        boolean usable = current != null && current.covers(ddnsHost(), now);
        if (usable && !current.renewalDue(now)) {
            if (!servingIssued) restartServer(); // e.g. obtained while the server was not running
            return;
        }
        if (now < certNextTry || closing) return;
        certResult = "Let's Encrypt から取得中…";
        try {
            KeyPair account = Acme.loadOrCreateAccountKey(new File(context.getFilesDir(), "acme-account.key"));
            KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
            kpg.initialize(2048, new SecureRandom());
            KeyPair certKey = kpg.generateKeyPair();
            List<X509Certificate> chain = new Acme(Acme.LETS_ENCRYPT, account, ddnsTls, 3000, 180_000)
                    .issue(ddnsHost(), certKey, duckDns(), TXT_PROPAGATION_MS);
            TlsIdentity issued = TlsIdentity.saveIssued(issuedFile(), certKey.getPrivate(), chain);
            certBackoff = CERT_RETRY_MIN_MS;
            certNextTry = 0;
            certResult = issued.describeIssued();
            restartServer();
        } catch (Exception e) {
            if (closing) return;
            Log.w(TAG, "certificate request failed", e);
            certNextTry = now + certBackoff;
            certBackoff = Math.min(certBackoff * 2, CERT_RETRY_MAX_MS);
            String why = e instanceof Acme.AcmeException ? ((Acme.AcmeException) e).userMessage : "証明書の取得に失敗しました";
            certResult = (usable ? current.describeIssued() + "・更新に失敗: " : "取得に失敗: ") + why
                    + (servingIssued ? "" : "（自己署名で公開中）");
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
        if (certResult != null) sb.append("\n証明書: ").append(certResult);
        if (fingerprint != null && !servingIssued) sb.append("\n証明書 ").append(fingerprint.substring(0, 23)).append('…');
        return sb.toString();
    }

    String statusJson() {
        return "{\"enabled\":" + settings.remoteEnabled + ",\"active\":" + active
                + ",\"state\":" + s(state) + ",\"url\":" + s(url()) + ",\"port\":" + settings.remotePort
                + ",\"error\":" + s(error != null ? error : ipWarning()) + ",\"fingerprint\":" + s(fingerprint)
                + ",\"wanIp\":" + s(wanIp) + ",\"upnp\":" + s(upnpResult) + ",\"ddns\":" + s(ddnsResult)
                + ",\"cert\":" + s(certResult) + ",\"certIssued\":" + servingIssued + "}";
    }

    private static String s(String v) {
        return v == null ? "null" : HttpServer.jsonString(v);
    }
}
