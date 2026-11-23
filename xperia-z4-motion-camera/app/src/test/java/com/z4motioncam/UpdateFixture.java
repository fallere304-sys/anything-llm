package com.z4motioncam;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsServer;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;

/** A local update server (plain or TLS), fake APKs, and a fake device for the update tests. */
final class UpdateFixture {
    /** Behaviour of one served path. */
    static final class Route {
        byte[] body = new byte[0];
        int status = 200;
        boolean supportRange = true;
        /** Close the connection after this many body bytes (-1 = serve everything). */
        long cutAfter = -1;
        String redirectTo;
        int hits;
        final List<String> ranges = new ArrayList<>();
    }

    final Map<String, Route> routes = new ConcurrentHashMap<>();
    final HttpServer server;
    final boolean tls;
    SSLSocketFactory clientTls;

    UpdateFixture(boolean tls) throws Exception {
        this.tls = tls;
        if (tls) {
            KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
            g.initialize(2048);
            KeyPair kp = g.generateKeyPair();
            X509Certificate cert = TlsIdentity.selfSign(kp, "update-test", 30, Arrays.asList("127.0.0.1"));
            KeyStore ks = KeyStore.getInstance("PKCS12");
            ks.load(null, null);
            ks.setKeyEntry("k", kp.getPrivate(), "pw".toCharArray(), new Certificate[] {cert});
            KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
            kmf.init(ks, "pw".toCharArray());
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(kmf.getKeyManagers(), null, null);
            HttpsServer https = HttpsServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            https.setHttpsConfigurator(new HttpsConfigurator(ctx));
            server = https;
            clientTls = NetUtil.clientTls(Arrays.asList(cert));
        } else {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        }
        server.createContext("/", new HttpHandler() {
            @Override
            public void handle(HttpExchange ex) throws IOException {
                serve(ex);
            }
        });
        server.start();
    }

    String url(String path) {
        return (tls ? "https" : "http") + "://127.0.0.1:" + server.getAddress().getPort() + path;
    }

    Route route(String path) {
        Route r = new Route();
        routes.put(path, r);
        return r;
    }

    void stop() {
        server.stop(0);
    }

    private void serve(HttpExchange ex) throws IOException {
        Route r = routes.get(ex.getRequestURI().getPath());
        try {
            if (r == null) {
                ex.sendResponseHeaders(404, -1);
                return;
            }
            r.hits++;
            String range = ex.getRequestHeaders().getFirst("Range");
            if (range != null) r.ranges.add(range);
            if (r.redirectTo != null) {
                ex.getResponseHeaders().add("Location", r.redirectTo);
                ex.sendResponseHeaders(302, -1);
                return;
            }
            if (r.status != 200) {
                ex.sendResponseHeaders(r.status, -1);
                return;
            }
            byte[] body = r.body;
            int from = 0;
            if (range != null && r.supportRange && range.startsWith("bytes=")) {
                from = Integer.parseInt(range.substring(6, range.indexOf('-')));
                if (from >= body.length) {
                    ex.getResponseHeaders().add("Content-Range", "bytes */" + body.length);
                    ex.sendResponseHeaders(416, -1);
                    return;
                }
                ex.getResponseHeaders().add("Content-Range", "bytes " + from + "-" + (body.length - 1) + "/" + body.length);
                ex.sendResponseHeaders(206, body.length - from);
            } else {
                ex.sendResponseHeaders(200, body.length);
            }
            OutputStream out = ex.getResponseBody();
            int n = body.length - from;
            if (r.cutAfter >= 0 && r.cutAfter < n) {
                out.write(body, from, (int) r.cutAfter);
                out.flush();
                ex.getHttpContext(); // then drop the connection with bytes missing
                ex.close();
                return;
            }
            out.write(body, from, n);
        } catch (IOException ignored) {
            // the test closed the connection on purpose
        } finally {
            ex.close();
        }
    }

    // ---- fake APKs ----

    /** A ZIP that looks like an APK; the "fake.info" entry says what the fake device should read from it. */
    static byte[] apk(String pkg, int versionCode, String signers, long seed) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        ZipOutputStream z = new ZipOutputStream(bytes);
        z.setLevel(0);
        put(z, "AndroidManifest.xml", new byte[] {3, 0, 8, 0});
        byte[] dex = new byte[40_000];
        new Random(seed).nextBytes(dex);
        put(z, "classes.dex", dex);
        put(z, "fake.info", ("pkg=" + pkg + "\nvc=" + versionCode + "\nsigners=" + signers + "\n").getBytes(StandardCharsets.UTF_8));
        z.close();
        return bytes.toByteArray();
    }

    private static void put(ZipOutputStream z, String name, byte[] data) throws IOException {
        z.putNextEntry(new ZipEntry(name));
        z.write(data);
        z.closeEntry();
    }

    static String sha(byte[] b) {
        try {
            return ApkCheck.hex(MessageDigest.getInstance("SHA-256").digest(b));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static byte[] manifest(String pkg, int vc, String name, String apkPath, byte[] apk, String shaOverride) {
        return ("{\"package\":\"" + pkg + "\",\"versionCode\":" + vc + ",\"versionName\":\"" + name + "\",\"apk\":\""
                + apkPath + "\",\"sha256\":\"" + (shaOverride != null ? shaOverride : sha(apk)) + "\",\"size\":" + apk.length
                + ",\"notes\":\"fix\"}").getBytes(StandardCharsets.UTF_8);
    }

    /** The device: version 11, signed by "aa". Reads the fake APKs. */
    static final class FakeEnv implements UpdateEngine.Env {
        int installed = 11;
        Set<String> signers = new HashSet<>(Arrays.asList("aa"));

        @Override
        public String packageName() {
            return "com.z4motioncam";
        }

        @Override
        public int installedVersionCode() {
            return installed;
        }

        @Override
        public long wallMs() {
            return System.currentTimeMillis();
        }

        @Override
        public Set<String> installedSigners() {
            return signers;
        }

        @Override
        public UpdateEngine.ApkInfo readApk(File apk) {
            try {
                java.util.zip.ZipFile z = new java.util.zip.ZipFile(apk);
                try {
                    ZipEntry e = z.getEntry("fake.info");
                    if (e == null) return null;
                    java.util.Properties p = new java.util.Properties();
                    p.load(z.getInputStream(e));
                    Set<String> s = new HashSet<>();
                    for (String x : p.getProperty("signers", "").split(",")) if (!x.isEmpty()) s.add(x);
                    return new UpdateEngine.ApkInfo(p.getProperty("pkg"), Integer.parseInt(p.getProperty("vc")), s);
                } finally {
                    z.close();
                }
            } catch (IOException | RuntimeException e) {
                return null;
            }
        }
    }
}
