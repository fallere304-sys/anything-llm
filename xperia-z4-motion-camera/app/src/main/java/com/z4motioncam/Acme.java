package com.z4motioncam;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.Charset;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.cert.X509Certificate;
import java.security.interfaces.RSAPrivateCrtKey;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.RSAPublicKeySpec;
import java.util.List;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLSocketFactory;

/**
 * Minimal ACME (RFC 8555) client: one DNS name, dns-01 challenge. Enough to get a Let's Encrypt
 * certificate for a DuckDNS name, whose TXT record DuckDNS lets the phone set over HTTPS. The other
 * challenge types need ports 80 / 443, which a MAP-E line usually cannot forward.
 *
 * <p>Why a public certificate at all: Google's UrlFetchApp (the spreadsheet viewer) refuses
 * self-signed certificates even with validateHttpsCertificates:false.
 */
final class Acme {
    static final String LETS_ENCRYPT = "https://acme-v02.api.letsencrypt.org/directory";
    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int TIMEOUT_MS = 20_000;

    /** Sets / removes the _acme-challenge TXT record of the name being certified. */
    interface Dns {
        void setTxt(String value) throws IOException;

        void clearTxt() throws IOException;
    }

    /** A failure with a message for the phone's status line (Japanese) and the server's detail. */
    static final class AcmeException extends IOException {
        final String userMessage;

        AcmeException(String userMessage, String detail) {
            super(userMessage + (detail == null ? "" : ": " + detail));
            this.userMessage = userMessage;
        }
    }

    private final String directoryUrl;
    private final KeyPair account;
    private final SSLSocketFactory tls;
    private final long pollMs;
    private final long pollTimeoutMs;
    private String nonce;
    private String kid;
    private String newNonceUrl;

    Acme(String directoryUrl, KeyPair account, SSLSocketFactory tls, long pollMs, long pollTimeoutMs) {
        this.directoryUrl = directoryUrl;
        this.account = account;
        this.tls = tls;
        this.pollMs = pollMs;
        this.pollTimeoutMs = pollTimeoutMs;
    }

    /**
     * Gets a certificate for {@code domain} with the key {@code certKey}. Registering the account
     * (or finding it again: same key, same account) agrees to the CA's terms of service.
     *
     * @param propagationMs wait between setting the TXT record and asking the CA to check it
     * @return the chain, leaf first
     */
    List<X509Certificate> issue(String domain, KeyPair certKey, Dns dns, long propagationMs)
            throws IOException, GeneralSecurityException {
        JSONObject dir = json(request("GET", directoryUrl, null), "directory");
        String newAccountUrl;
        String newOrderUrl;
        try {
            newNonceUrl = dir.getString("newNonce");
            newAccountUrl = dir.getString("newAccount");
            newOrderUrl = dir.getString("newOrder");
        } catch (JSONException e) {
            throw new AcmeException("認証局の応答が不正です", e.getMessage());
        }

        kid = null;
        Resp acc = post(newAccountUrl, "{\"termsOfServiceAgreed\":true}");
        if (acc.location == null) throw new AcmeException("認証局の応答が不正です", "no account URL");
        kid = acc.location;

        Resp orderResp = post(newOrderUrl,
                "{\"identifiers\":[{\"type\":\"dns\",\"value\":" + HttpServer.jsonString(domain) + "}]}");
        String orderUrl = orderResp.location;
        if (orderUrl == null) throw new AcmeException("認証局の応答が不正です", "no order URL");
        JSONObject order = json(orderResp, "order");

        boolean txtSet = false;
        try {
            JSONArray auths = order.optJSONArray("authorizations");
            for (int i = 0; auths != null && i < auths.length(); i++) {
                String authUrl = auths.getString(i);
                JSONObject auth = json(post(authUrl, null), "authorization");
                if ("valid".equals(auth.optString("status"))) continue;
                JSONObject ch = challenge(auth, "dns-01");
                if (ch == null) throw new AcmeException("認証局が DNS による確認に対応していません", null);
                String keyAuth = ch.getString("token") + "." + thumbprint();
                dns.setTxt(b64u(sha256(keyAuth.getBytes(UTF8))));
                txtSet = true;
                sleep(propagationMs);
                post(ch.getString("url"), "{}");
                auth = poll(authUrl, "authorization");
                if (!"valid".equals(auth.optString("status"))) {
                    JSONObject c = challenge(auth, "dns-01");
                    JSONObject err = c == null ? null : c.optJSONObject("error");
                    throw new AcmeException("ドメインの確認に失敗しました（DuckDNS のドメイン名とトークンを確認）",
                            err == null ? auth.optString("status") : err.optString("detail"));
                }
            }

            order = json(post(orderUrl, null), "order");
            byte[] csr = TlsIdentity.csr(certKey, domain);
            post(order.getString("finalize"), "{\"csr\":\"" + b64u(csr) + "\"}");
            order = poll(orderUrl, "order");
            if (!"valid".equals(order.optString("status"))) {
                JSONObject err = order.optJSONObject("error");
                throw new AcmeException("証明書を発行できませんでした", err == null ? order.optString("status") : err.optString("detail"));
            }
            Resp cert = post(order.getString("certificate"), null);
            List<X509Certificate> chain;
            try {
                chain = NetUtil.parsePem(cert.body);
            } catch (GeneralSecurityException e) {
                throw new AcmeException("受け取った証明書を読めません", e.getMessage());
            }
            if (chain.isEmpty() || !chain.get(0).getPublicKey().equals(certKey.getPublic())) {
                throw new AcmeException("受け取った証明書が鍵と一致しません", null);
            }
            return chain;
        } catch (JSONException e) {
            throw new AcmeException("認証局の応答が不正です", e.getMessage());
        } finally {
            if (txtSet) {
                try {
                    dns.clearTxt();
                } catch (IOException ignored) {
                    // harmless: the next attempt overwrites it
                }
            }
        }
    }

    private static JSONObject challenge(JSONObject auth, String type) throws JSONException {
        JSONArray chs = auth.optJSONArray("challenges");
        for (int i = 0; chs != null && i < chs.length(); i++) {
            if (type.equals(chs.getJSONObject(i).optString("type"))) return chs.getJSONObject(i);
        }
        return null;
    }

    /** POST-as-GET until the object leaves "pending" / "ready" / "processing". */
    private JSONObject poll(String url, String what) throws IOException, GeneralSecurityException {
        long deadline = System.currentTimeMillis() + pollTimeoutMs;
        while (true) {
            JSONObject o = json(post(url, null), what);
            String st = o.optString("status");
            if (!st.equals("pending") && !st.equals("processing") && !(what.equals("order") && st.equals("ready"))) {
                return o;
            }
            if (System.currentTimeMillis() > deadline) throw new AcmeException("認証局の確認が時間内に終わりませんでした", st);
            sleep(pollMs);
        }
    }

    /** The account key (RSA 2048), kept in app-private storage; the same key means the same account. */
    static KeyPair loadOrCreateAccountKey(File file) throws IOException, GeneralSecurityException {
        KeyFactory kf = KeyFactory.getInstance("RSA");
        if (file.isFile()) {
            try {
                InputStream in = new FileInputStream(file);
                byte[] der;
                try {
                    der = HttpServer.readAll(in);
                } finally {
                    in.close();
                }
                PrivateKey key = kf.generatePrivate(new PKCS8EncodedKeySpec(der));
                if (key instanceof RSAPrivateCrtKey) {
                    RSAPrivateCrtKey k = (RSAPrivateCrtKey) key;
                    PublicKey pub = kf.generatePublic(new RSAPublicKeySpec(k.getModulus(), k.getPublicExponent()));
                    return new KeyPair(pub, key);
                }
            } catch (IOException | GeneralSecurityException corrupt) {
                // create a new account below
            }
        }
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048, new SecureRandom());
        KeyPair kp = kpg.generateKeyPair();
        File tmp = new File(file.getPath() + ".tmp");
        OutputStream out = new FileOutputStream(tmp);
        try {
            out.write(kp.getPrivate().getEncoded()); // PKCS#8
        } finally {
            out.close();
        }
        if (!tmp.renameTo(file)) throw new IOException("cannot save " + file);
        return kp;
    }

    // ---- transport ----

    private static final class Resp {
        int code;
        String body = "";
        String location;
        String nonce;
    }

    private Resp request(String method, String url, byte[] body) throws IOException {
        HttpURLConnection c;
        try {
            c = (HttpURLConnection) new URL(url).openConnection();
        } catch (IOException e) {
            throw new AcmeException("認証局に接続できません", e.getMessage());
        }
        try {
            if (tls != null && c instanceof HttpsURLConnection) ((HttpsURLConnection) c).setSSLSocketFactory(tls);
            c.setConnectTimeout(TIMEOUT_MS);
            c.setReadTimeout(TIMEOUT_MS);
            c.setUseCaches(false);
            c.setInstanceFollowRedirects(false);
            c.setRequestMethod(method);
            c.setRequestProperty("User-Agent", "Z4MotionCam");
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/jose+json");
                c.setFixedLengthStreamingMode(body.length);
                OutputStream out = c.getOutputStream();
                out.write(body);
                out.close();
            }
            Resp r = new Resp();
            r.code = c.getResponseCode();
            r.location = c.getHeaderField("Location");
            r.nonce = c.getHeaderField("Replay-Nonce");
            if (!"HEAD".equals(method)) {
                InputStream in = r.code >= 400 ? c.getErrorStream() : c.getInputStream();
                if (in != null) r.body = NetUtil.readLimited(in, 256 * 1024);
            }
            if (r.nonce != null) nonce = r.nonce;
            return r;
        } catch (AcmeException e) {
            throw e;
        } catch (IOException e) {
            throw new AcmeException("認証局に接続できません", e.getMessage());
        } finally {
            c.disconnect();
        }
    }

    /** Signed POST ({@code payload} null = POST-as-GET); retries a rejected nonce. */
    private Resp post(String url, String payload) throws IOException, GeneralSecurityException {
        for (int attempt = 0; ; attempt++) {
            if (nonce == null) {
                request("HEAD", newNonceUrl, null);
                if (nonce == null) throw new AcmeException("認証局の応答が不正です", "no nonce");
            }
            String n = nonce;
            nonce = null;
            Resp r = request("POST", url, jws(url, n, payload));
            if (r.code < 400) return r;
            String type = "";
            String detail = r.body;
            try {
                JSONObject p = new JSONObject(r.body);
                type = p.optString("type");
                detail = p.optString("detail", r.body);
            } catch (JSONException ignored) {
                // not a problem document
            }
            if (type.endsWith(":badNonce") && attempt < 10) continue;
            if (type.endsWith(":rateLimited")) throw new AcmeException("発行回数の上限に達しました（時間をおいて自動で再試行します）", detail);
            throw new AcmeException("認証局がエラーを返しました（HTTP " + r.code + "）", detail);
        }
    }

    private byte[] jws(String url, String n, String payload) throws GeneralSecurityException {
        String header = "{\"alg\":\"RS256\"," + (kid == null ? "\"jwk\":" + jwk() : "\"kid\":" + HttpServer.jsonString(kid))
                + ",\"nonce\":" + HttpServer.jsonString(n) + ",\"url\":" + HttpServer.jsonString(url) + "}";
        String h64 = b64u(header.getBytes(UTF8));
        String p64 = payload == null ? "" : b64u(payload.getBytes(UTF8));
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initSign(account.getPrivate());
        s.update((h64 + "." + p64).getBytes(UTF8));
        String sig = b64u(s.sign());
        return ("{\"protected\":\"" + h64 + "\",\"payload\":\"" + p64 + "\",\"signature\":\"" + sig + "\"}").getBytes(UTF8);
    }

    /** The account's public key as a JWK, members in lexicographic order (RFC 7638). */
    private String jwk() {
        RSAPublicKey k = (RSAPublicKey) account.getPublic();
        return "{\"e\":\"" + b64u(unsigned(k.getPublicExponent())) + "\",\"kty\":\"RSA\",\"n\":\""
                + b64u(unsigned(k.getModulus())) + "\"}";
    }

    String thumbprint() throws GeneralSecurityException {
        return b64u(sha256(jwk().getBytes(UTF8)));
    }

    private static JSONObject json(Resp r, String what) throws AcmeException {
        if (r.code / 100 != 2) throw new AcmeException("認証局がエラーを返しました（HTTP " + r.code + "）", what);
        try {
            return new JSONObject(r.body);
        } catch (JSONException e) {
            throw new AcmeException("認証局の応答が不正です", what);
        }
    }

    private void sleep(long ms) throws IOException {
        if (ms <= 0) return;
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted");
        }
    }

    // ---- encoding ----

    static byte[] sha256(byte[] data) throws GeneralSecurityException {
        return MessageDigest.getInstance("SHA-256").digest(data);
    }

    private static byte[] unsigned(BigInteger v) {
        byte[] b = v.toByteArray();
        if (b.length > 1 && b[0] == 0) {
            byte[] t = new byte[b.length - 1];
            System.arraycopy(b, 1, t, 0, t.length);
            return t;
        }
        return b;
    }

    private static final char[] B64U = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".toCharArray();

    /** Base64url without padding (java.util.Base64 needs API 26). */
    static String b64u(byte[] data) {
        StringBuilder sb = new StringBuilder((data.length * 4 + 2) / 3);
        int i = 0;
        for (; i + 2 < data.length; i += 3) {
            int v = (data[i] & 0xFF) << 16 | (data[i + 1] & 0xFF) << 8 | (data[i + 2] & 0xFF);
            sb.append(B64U[v >> 18]).append(B64U[(v >> 12) & 63]).append(B64U[(v >> 6) & 63]).append(B64U[v & 63]);
        }
        int rest = data.length - i;
        if (rest == 1) {
            int v = (data[i] & 0xFF) << 16;
            sb.append(B64U[v >> 18]).append(B64U[(v >> 12) & 63]);
        } else if (rest == 2) {
            int v = (data[i] & 0xFF) << 16 | (data[i + 1] & 0xFF) << 8;
            sb.append(B64U[v >> 18]).append(B64U[(v >> 12) & 63]).append(B64U[(v >> 6) & 63]);
        }
        return sb.toString();
    }
}
