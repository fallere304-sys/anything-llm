package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;
import static org.junit.Assume.assumeTrue;

import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManagerFactory;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/**
 * The ACME client against Pebble, Let's Encrypt's test CA, with pebble-challtestsrv as the DNS the
 * CA queries and a stand-in for the DuckDNS update API in front of it. Runs when PEBBLE_CA points
 * to the CA certificate of Pebble's own HTTPS endpoint (see README "Tests"); skipped otherwise.
 * Pebble rejects 5% of nonces on purpose, so the badNonce retry is exercised too.
 */
public class AcmeTest {
    private static final String DIRECTORY = "https://127.0.0.1:14000/dir";
    private static final String CHALLTESTSRV = "http://127.0.0.1:8055";
    private static final String DOMAIN = "testcam.duckdns.org";
    private static final String TOKEN = "11111111-2222-3333-4444-555555555555";

    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    @Test
    public void base64urlWithoutPadding() {
        assertEquals("", Acme.b64u(new byte[0]));
        assertEquals("Zg", Acme.b64u("f".getBytes(StandardCharsets.US_ASCII)));
        assertEquals("Zm8", Acme.b64u("fo".getBytes(StandardCharsets.US_ASCII)));
        assertEquals("Zm9v", Acme.b64u("foo".getBytes(StandardCharsets.US_ASCII)));
        assertEquals("-_8", Acme.b64u(new byte[] {(byte) 0xFB, (byte) 0xFF}));
    }

    @Test
    public void thumbprintMatchesRfc7638Example() throws Exception {
        // RFC 7638 section 3.1.
        String n = "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECP"
                + "ebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY"
                + "368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0f"
                + "M4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw";
        java.math.BigInteger mod = new java.math.BigInteger(1, HttpServer64.decode(n));
        java.security.PublicKey pub = java.security.KeyFactory.getInstance("RSA")
                .generatePublic(new java.security.spec.RSAPublicKeySpec(mod, java.math.BigInteger.valueOf(65537)));
        Acme acme = new Acme(DIRECTORY, new KeyPair(pub, null), null, 0, 0);
        assertEquals("NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs", acme.thumbprint());
    }

    @Test
    public void accountKeyIsKeptAcrossRestarts() throws Exception {
        File f = new File(tmp.getRoot(), "acme-account.key");
        KeyPair a = Acme.loadOrCreateAccountKey(f);
        KeyPair b = Acme.loadOrCreateAccountKey(f);
        assertEquals(a.getPublic(), b.getPublic());
        Files.write(f.toPath(), new byte[] {1, 2, 3}); // corrupt: a new account
        assertFalse(a.getPublic().equals(Acme.loadOrCreateAccountKey(f).getPublic()));
    }

    @Test
    public void csrNamesTheDomainAndIsSigned() throws Exception {
        KeyPair kp = rsa();
        byte[] csr = TlsIdentity.csr(kp, "MyBaby.duckdns.org");
        // The DER parses as SEQUENCE { info, alg, sig } and the signature verifies.
        byte[][] parts = children(csr);
        assertEquals(3, parts.length);
        java.security.Signature s = java.security.Signature.getInstance("SHA256withRSA");
        s.initVerify(kp.getPublic());
        s.update(parts[0]);
        byte[] bits = contents(parts[2]);
        assertTrue(s.verify(Arrays.copyOfRange(bits, 1, bits.length)));
        assertTrue(new String(csr, StandardCharsets.ISO_8859_1).contains("mybaby.duckdns.org"));
    }

    @Test
    public void issuesCertificateThroughDuckDnsTxtAndServesIt() throws Exception {
        String caPath = System.getenv("PEBBLE_CA");
        assumeTrue("PEBBLE_CA not set: Pebble test skipped", caPath != null);
        SSLSocketFactory pebbleTls = trusting(load(new File(caPath)));
        FakeDuckDns duck = new FakeDuckDns();
        try {
            KeyPair account = Acme.loadOrCreateAccountKey(new File(tmp.getRoot(), "acme-account.key"));
            KeyPair certKey = rsa();
            DuckDns dns = new DuckDns(duck.url(), "testcam", TOKEN, null);
            List<X509Certificate> chain = new Acme(DIRECTORY, account, pebbleTls, 200, 60_000)
                    .issue(DOMAIN, certKey, dns, 0);

            assertTrue(chain.size() >= 2); // leaf + intermediate
            X509Certificate leaf = chain.get(0);
            assertEquals(certKey.getPublic(), leaf.getPublicKey());
            assertEquals(Arrays.asList(2, DOMAIN), new ArrayList<>(leaf.getSubjectAlternativeNames().iterator().next()));
            assertTrue("TXT set through DuckDNS", duck.calls.get(0).startsWith("txt="));
            assertTrue("TXT removed afterwards", duck.calls.get(duck.calls.size() - 1).contains("clear=true"));

            // Stored, reloaded and served by the HTTPS server; a client trusting Pebble's root accepts it.
            File store = new File(tmp.getRoot(), "tls-issued.keystore");
            TlsIdentity.saveIssued(store, certKey.getPrivate(), chain);
            TlsIdentity id = TlsIdentity.loadIssued(store);
            assertNotNull(id);
            long now = System.currentTimeMillis();
            assertTrue(id.covers(DOMAIN, now));
            assertTrue(id.covers("TESTCAM.duckdns.org", now));
            assertFalse(id.covers("other.duckdns.org", now));
            assertFalse(id.renewalDue(now));
            assertTrue(id.renewalDue(leaf.getNotAfter().getTime() - 86_400_000L));

            X509Certificate root = pebbleRoot(pebbleTls);
            z4Server(id, root);

            // Same account key: the CA finds the existing account again.
            List<X509Certificate> again = new Acme(DIRECTORY, account, pebbleTls, 200, 60_000)
                    .issue(DOMAIN, rsa(), dns, 0);
            assertFalse(again.get(0).getSerialNumber().equals(leaf.getSerialNumber()));
        } finally {
            duck.stop();
        }
    }

    @Test
    public void wrongDuckDnsTokenGivesJapaneseReason() throws Exception {
        String caPath = System.getenv("PEBBLE_CA");
        assumeTrue("PEBBLE_CA not set: Pebble test skipped", caPath != null);
        SSLSocketFactory pebbleTls = trusting(load(new File(caPath)));
        FakeDuckDns duck = new FakeDuckDns();
        try {
            DuckDns dns = new DuckDns(duck.url(), "testcam", "wrong-token", null);
            new Acme(DIRECTORY, rsa(), pebbleTls, 200, 60_000).issue(DOMAIN, rsa(), dns, 0);
            fail();
        } catch (Acme.AcmeException e) {
            assertEquals("DuckDNS に確認用の値を設定できません（ドメイン名とトークンを確認）", e.userMessage);
        } finally {
            duck.stop();
        }
    }

    @Test
    public void unreachableCaGivesJapaneseReason() throws Exception {
        try {
            new Acme("https://127.0.0.1:1/dir", rsa(), null, 0, 0).issue(DOMAIN, rsa(), null, 0);
            fail();
        } catch (Acme.AcmeException e) {
            assertEquals("認証局に接続できません", e.userMessage);
        }
    }

    // ---- helpers ----

    /** The HTTPS server of the app with the issued identity; checked with host name verification. */
    private static void z4Server(TlsIdentity id, X509Certificate root) throws Exception {
        com.z4motioncam.HttpServer.Backend backend = new com.z4motioncam.HttpServer.Backend() {
            @Override public byte[] indexHtml() { return new byte[0]; }
            @Override public String statusJson() { return "{}"; }
            @Override public RecordingStore store() { return null; }
            @Override public FrameHub frames() { return null; }
            @Override public String password() { return "pw123456"; }
            @Override public String uptimeJson() { return "{}"; }
            @Override public boolean monitoring() { return true; }
            @Override public void setMonitoring(boolean on) { }
            @Override public String settingsJson() { return "{}"; }
            @Override public String updateSettings(Map<String, String> changes) { return null; }
        };
        com.z4motioncam.HttpServer server = new com.z4motioncam.HttpServer(0, backend, id.serverSocketFactory(), false, true);
        server.start();
        try {
            HttpsURLConnection c = (HttpsURLConnection) new URL("https://localhost:" + server.port() + "/api/status")
                    .openConnection(java.net.Proxy.NO_PROXY);
            c.setSSLSocketFactory(trusting(root));
            final int[] sent = {0};
            c.setHostnameVerifier((host, session) -> {
                try {
                    sent[0] = session.getPeerCertificates().length;
                    X509Certificate leaf = (X509Certificate) session.getPeerCertificates()[0];
                    return leaf.getSubjectAlternativeNames().iterator().next().get(1).equals(DOMAIN);
                } catch (Exception e) {
                    return false;
                }
            });
            assertEquals(401, c.getResponseCode()); // TLS accepted by a verifying client; password still required
            assertTrue(sent[0] >= 2); // the intermediate is sent too
        } finally {
            server.stop();
        }
    }

    private static X509Certificate pebbleRoot(SSLSocketFactory tls) throws Exception {
        HttpsURLConnection c = (HttpsURLConnection) new URL("https://127.0.0.1:15000/roots/0").openConnection(java.net.Proxy.NO_PROXY);
        c.setSSLSocketFactory(tls);
        InputStream in = c.getInputStream();
        try {
            return (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(in);
        } finally {
            in.close();
        }
    }

    private static X509Certificate load(File pem) throws Exception {
        return (X509Certificate) CertificateFactory.getInstance("X.509")
                .generateCertificate(new ByteArrayInputStream(Files.readAllBytes(pem.toPath())));
    }

    private static SSLSocketFactory trusting(X509Certificate ca) throws Exception {
        KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
        ks.load(null, null);
        ks.setCertificateEntry("ca", ca);
        TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        tmf.init(ks);
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, tmf.getTrustManagers(), null);
        return ctx.getSocketFactory();
    }

    private static KeyPair rsa() throws Exception {
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        return kpg.generateKeyPair();
    }

    private static byte[][] children(byte[] der) {
        byte[] body = contents(der);
        List<byte[]> out = new ArrayList<>();
        int i = 0;
        while (i < body.length) {
            int[] hl = headerAndLength(body, i);
            out.add(Arrays.copyOfRange(body, i, i + hl[0] + hl[1]));
            i += hl[0] + hl[1];
        }
        return out.toArray(new byte[0][]);
    }

    private static byte[] contents(byte[] tlv) {
        int[] hl = headerAndLength(tlv, 0);
        return Arrays.copyOfRange(tlv, hl[0], hl[0] + hl[1]);
    }

    /** {header length, content length} of the TLV at {@code i}. */
    private static int[] headerAndLength(byte[] b, int i) {
        int len = b[i + 1] & 0xFF;
        if (len < 0x80) return new int[] {2, len};
        int n = len & 0x7F;
        int v = 0;
        for (int k = 0; k < n; k++) v = (v << 8) | (b[i + 2 + k] & 0xFF);
        return new int[] {2 + n, v};
    }

    /** Base64url decoding for the RFC example (the app's decoder accepts both alphabets). */
    private static final class HttpServer64 {
        static byte[] decode(String s) {
            return com.z4motioncam.HttpServer.Base64.decode(s);
        }
    }

    /**
     * Stands in for www.duckdns.org/update: checks the token and puts the TXT value into
     * pebble-challtestsrv, which answers the CA's DNS queries.
     */
    private static final class FakeDuckDns {
        final List<String> calls = new ArrayList<>();
        private final HttpServer server;

        FakeDuckDns() throws Exception {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/update", ex -> {
                Map<String, String> q = new HashMap<>();
                for (String kv : ex.getRequestURI().getRawQuery().split("&")) {
                    String[] p = kv.split("=", 2);
                    q.put(p[0], p.length > 1 ? URLDecoder.decode(p[1], "UTF-8") : "");
                }
                String answer = "KO";
                if ("testcam".equals(q.get("domains")) && TOKEN.equals(q.get("token")) && q.containsKey("txt")) {
                    String host = "_acme-challenge." + DOMAIN + ".";
                    try {
                        if ("true".equals(q.get("clear"))) {
                            calls.add("clear=true");
                            challtestsrv("/clear-txt", "{\"host\":\"" + host + "\"}");
                        } else {
                            calls.add("txt=" + q.get("txt"));
                            challtestsrv("/set-txt", "{\"host\":\"" + host + "\",\"value\":\"" + q.get("txt") + "\"}");
                        }
                        answer = "OK";
                    } catch (Exception e) {
                        answer = "KO";
                    }
                }
                byte[] b = answer.getBytes(StandardCharsets.UTF_8);
                ex.sendResponseHeaders(200, b.length);
                ex.getResponseBody().write(b);
                ex.close();
            });
            server.start();
        }

        String url() {
            return "http://127.0.0.1:" + server.getAddress().getPort() + "/update";
        }

        void stop() {
            server.stop(0);
        }

        private static void challtestsrv(String path, String json) throws Exception {
            HttpURLConnection c = (HttpURLConnection) new URL(CHALLTESTSRV + path).openConnection(java.net.Proxy.NO_PROXY);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            OutputStream out = c.getOutputStream();
            out.write(json.getBytes(StandardCharsets.UTF_8));
            out.close();
            if (c.getResponseCode() != 200) throw new IllegalStateException("challtestsrv " + c.getResponseCode());
        }
    }
}
