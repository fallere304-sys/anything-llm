package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.cert.X509Certificate;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class TlsIdentityTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private static final java.util.List<String> HOSTS = java.util.Arrays.asList("203.0.113.5", "mybaby.duckdns.org", "127.0.0.1");

    @Test
    public void createsIosCompliantCertificateAndReloadsIt() throws Exception {
        File f = new File(tmp.getRoot(), "tls.keystore");
        TlsIdentity id = TlsIdentity.loadOrCreate(f, HOSTS);
        X509Certificate c = id.certificate;
        assertEquals(3, c.getVersion());
        assertEquals("SHA256withRSA", c.getSigAlgName());
        c.checkValidity();
        c.verify(c.getPublicKey()); // self-signature is correct
        assertEquals(-1, c.getBasicConstraints()); // not a CA
        assertTrue(c.getSubjectX500Principal().getName().contains("CN=Z4 MotionCam"));
        // Apple: serverAuth EKU, validity <= 825 days, host names in subjectAltName.
        assertTrue(c.getExtendedKeyUsage().contains("1.3.6.1.5.5.7.3.1"));
        assertTrue(c.getNotAfter().getTime() - c.getNotBefore().getTime() <= 825L * 86_400_000L);
        java.util.Set<String> sans = new java.util.HashSet<>();
        for (java.util.List<?> san : c.getSubjectAlternativeNames()) sans.add(san.get(0) + ":" + san.get(1));
        assertEquals(new java.util.HashSet<>(java.util.Arrays.asList("7:203.0.113.5", "2:mybaby.duckdns.org", "7:127.0.0.1")), sans);
        assertTrue(c.getKeyUsage()[0] && c.getKeyUsage()[2]); // digitalSignature, keyEncipherment
        assertEquals(95, id.fingerprint().length());
        assertEquals("same identity after restart", id.fingerprint(), TlsIdentity.loadOrCreate(f, HOSTS).fingerprint());
        assertEquals("a host that is momentarily unknown keeps the certificate", id.fingerprint(),
                TlsIdentity.loadOrCreate(f, java.util.Arrays.asList("203.0.113.5")).fingerprint());
        assertFalse("a new outside address needs a new certificate", id.fingerprint().equals(
                TlsIdentity.loadOrCreate(f, java.util.Arrays.asList("198.51.100.7")).fingerprint()));
    }

    @Test
    public void replacesOldNonCompliantCertificate() throws Exception {
        java.security.KeyPairGenerator kpg = java.security.KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        java.security.KeyPair kp = kpg.generateKeyPair();
        // Like v1.2-1.4: ten years, no subjectAltName.
        X509Certificate old = TlsIdentity.selfSign(kp, "Z4 MotionCam", 3650, java.util.Collections.<String>emptyList());
        assertFalse(TlsIdentity.isUsable(old, HOSTS, System.currentTimeMillis()));
        assertFalse(TlsIdentity.isUsable(old, java.util.Collections.<String>emptyList(), System.currentTimeMillis()));
    }

    @Test
    public void strictClientAcceptsItForTheListedAddress() throws Exception {
        // A client that trusts this certificate and checks the host name, as browsers do.
        TlsIdentity id = TlsIdentity.loadOrCreate(new File(tmp.getRoot(), "tls.keystore"), HOSTS);
        final RecordingStore store = new RecordingStore(tmp.newFolder("rec2"), 0);
        final FrameHub hub = new FrameHub();
        password = "longenough";
        HttpServer server = new HttpServer(0, new HttpServer.Backend() {
            public byte[] indexHtml() { return new byte[0]; }
            public String statusJson() { return "{\"ok\":true}"; }
            public RecordingStore store() { return store; }
            public FrameHub frames() { return hub; }
            public String password() { return password; }
            public String uptimeJson() { return "{}"; }
            public String settingsJson() { return "{}"; }
            public String updateSettings(java.util.Map<String, String> c) { return null; }
        }, id.serverSocketFactory(), false, true);
        server.start();
        try {
            java.security.KeyStore trust = java.security.KeyStore.getInstance(java.security.KeyStore.getDefaultType());
            trust.load(null, null);
            trust.setCertificateEntry("z4", id.certificate);
            javax.net.ssl.TrustManagerFactory tmf = javax.net.ssl.TrustManagerFactory.getInstance(
                    javax.net.ssl.TrustManagerFactory.getDefaultAlgorithm());
            tmf.init(trust);
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, tmf.getTrustManagers(), null);
            javax.net.ssl.HttpsURLConnection c = (javax.net.ssl.HttpsURLConnection)
                    new java.net.URL("https://127.0.0.1:" + server.port() + "/api/status").openConnection();
            c.setSSLSocketFactory(ctx.getSocketFactory()); // default hostname verifier stays on
            c.setRequestProperty("Authorization", "Basic dTpsb25nZW5vdWdo");
            assertEquals(200, c.getResponseCode());
        } finally {
            server.stop();
            hub.close();
        }
    }

    private String password = "";

    private SSLSocket connect(int port, String expectedFingerprint) throws Exception {
        final String[] seen = new String[1];
        TrustManager pinning = new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] chain, String a) {}
            public void checkServerTrusted(X509Certificate[] chain, String a) {
                try {
                    byte[] d = java.security.MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < d.length; i++) sb.append(i > 0 ? ":" : "").append(String.format("%02X", d[i] & 0xFF));
                    seen[0] = sb.toString();
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            }
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        };
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[] {pinning}, null);
        SSLSocket s = (SSLSocket) ctx.getSocketFactory().createSocket("127.0.0.1", port);
        s.setSoTimeout(5000);
        s.startHandshake();
        assertEquals(expectedFingerprint, seen[0]);
        return s;
    }

    private String request(int port, String fp, String authHeader) throws Exception {
        SSLSocket s = connect(port, fp);
        try {
            String req = "GET /api/status HTTP/1.1\r\nHost: x\r\n" + (authHeader == null ? "" : authHeader + "\r\n") + "\r\n";
            s.getOutputStream().write(req.getBytes(StandardCharsets.UTF_8));
            InputStream in = s.getInputStream();
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int n;
            try {
                while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            } catch (IOException closed) {
                // server closed the TLS connection without close_notify
            }
            assertEquals("TLSv1.2 or newer", true, s.getSession().getProtocol().compareTo("TLSv1.2") >= 0);
            return new String(b.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            s.close();
        }
    }

    @Test
    public void httpsServerRequiresPassword() throws Exception {
        TlsIdentity id = TlsIdentity.loadOrCreate(new File(tmp.getRoot(), "tls.keystore"), HOSTS);
        final RecordingStore store = new RecordingStore(tmp.newFolder("rec"), 0);
        final FrameHub hub = new FrameHub();
        HttpServer server = new HttpServer(0, new HttpServer.Backend() {
            public byte[] indexHtml() { return new byte[0]; }
            public String statusJson() { return "{\"ok\":true}"; }
            public RecordingStore store() { return store; }
            public FrameHub frames() { return hub; }
            public String password() { return password; }
            public String uptimeJson() { return "{}"; }
            public String settingsJson() { return "{}"; }
            public String updateSettings(java.util.Map<String, String> c) { return null; }
        }, id.serverSocketFactory(), false, true);
        server.start();
        try {
            int port = server.port();
            String fp = id.fingerprint();
            assertTrue("no password -> refuse everything", request(port, fp, null).startsWith("HTTP/1.0 403"));
            password = "longenough";
            assertTrue(request(port, fp, null).startsWith("HTTP/1.0 401"));
            // "u:longenough"
            String ok = request(port, fp, "Authorization: Basic dTpsb25nZW5vdWdo");
            assertTrue(ok, ok.startsWith("HTTP/1.0 200") && ok.endsWith("{\"ok\":true}"));
        } finally {
            server.stop();
            hub.close();
        }
    }
}
