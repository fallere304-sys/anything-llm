package com.z4motioncam;

import static com.z4motioncam.UpdatePushFixture.bytes;
import static com.z4motioncam.UpdatePushFixture.fx;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/**
 * The browser entrance of the update: the engine's receive step, and the whole path over real
 * sockets (LAN server and password-protected Internet server) with signed test APKs.
 */
public class UpdatePushTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private UpdatePushFixture.Env env;
    private UpdateStore store;
    private UpdateEngine engine;
    private UpdateWindow window;
    private UpdatePush push;
    private final AtomicInteger entered = new AtomicInteger();
    private volatile String busyReason;
    private volatile UpdateManifest left;
    private HttpServer lan;
    private HttpServer remote;
    private FrameHub hub;
    private int target;
    private long nowMs = 1_000;

    @Before
    public void setUp() throws IOException {
        env = new UpdatePushFixture.Env();
        store = new UpdateStore(tmp.newFolder("updates"));
        engine = new UpdateEngine(store, env, new UpdateHttp(null, true));
        window = new UpdateWindow(new Random(11));
        push = new UpdatePush(engine, window, new UpdatePush.Gate() {
            @Override
            public String enter() {
                if (busyReason != null) return busyReason;
                entered.incrementAndGet();
                return null;
            }

            @Override
            public void leave(UpdateManifest received) {
                left = received;
            }
        });
        hub = new FrameHub();
        final RecordingStore rec = new RecordingStore(tmp.newFolder("rec"), 0);
        HttpServer.Backend backend = new HttpServer.Backend() {
            @Override public byte[] indexHtml() { return new byte[0]; }
            @Override public String statusJson() { return "{}"; }
            @Override public RecordingStore store() { return rec; }
            @Override public FrameHub frames() { return hub; }
            @Override public String password() { return "correct-horse"; }
            @Override public String uptimeJson() { return "{}"; }
            @Override public boolean monitoring() { return true; }
            @Override public void setMonitoring(boolean on) { }
            @Override public String settingsJson() { return "{}"; }
            @Override public String updateSettings(Map<String, String> c) { return null; }
            @Override public String updateJson() { return "{\"push\":{\"open\":" + window.isOpen(nowMs) + "}}"; }
            @Override public String updateAction(String a) { return null; }
            @Override public UpdatePush.Result acceptUpdate(String code, InputStream body, long length) {
                return push.receive(code, body, length, nowMs);
            }
        };
        lan = new HttpServer(0, backend, null, true, false);
        lan.start();
        remote = new HttpServer(0, backend, null, false, true);
        remote.start();
        target = lan.port();
    }

    @After
    public void tearDown() {
        hub.close();
        lan.stop();
        remote.stop();
    }

    private String[] leftovers() {
        String[] names = store.dir().list();
        java.util.Arrays.sort(names);
        return names;
    }

    // ---- engine: receive ----

    private UpdateException rejected(byte[] apk, long length, UpdateException.Kind kind) throws Exception {
        try {
            engine.receive(new ByteArrayInputStream(apk), length);
        } catch (UpdateException e) {
            assertEquals(e.userMessage, kind, e.kind);
            for (String n : leftovers()) assertFalse("left behind: " + n, n.endsWith(".part") || n.endsWith(".apk"));
            return e;
        }
        fail("expected " + kind);
        return null;
    }

    @Test
    public void aGoodApkBecomesTheReadyUpdateAndPassesTheSameFinalCheckAsADownloadedOne() throws Exception {
        byte[] apk = bytes(fx("signed-a2.apk"));
        UpdateManifest m = engine.receive(new ByteArrayInputStream(apk), apk.length);
        assertEquals(12, m.versionCode);
        assertEquals("1.12", m.versionName);
        assertEquals(ApkCheck.sha256Hex(apk), m.sha256);
        assertEquals(apk.length, m.size);
        assertTrue(m.isPushed());
        assertTrue(store.ready(12).isFile());
        assertEquals(12, store.newestReady(11));
        UpdateStore.Meta meta = store.readMeta(12);
        assertEquals(UpdateManifest.PUSH_URL, meta.url);
        assertEquals("1.12", meta.name);
        assertEquals(store.ready(12), engine.verifyReady(12)); // what the install button runs
    }

    @Test
    public void readyFileChangedAfterwardsIsRefusedAtInstallTime() throws Exception {
        byte[] apk = bytes(fx("signed-a2.apk"));
        engine.receive(new ByteArrayInputStream(apk), apk.length);
        FileOutputStream out = new FileOutputStream(store.ready(12), true);
        out.write(1);
        out.close();
        try {
            engine.verifyReady(12);
            fail("expected a refusal");
        } catch (UpdateException e) {
            assertTrue(e.userMessage, e.kind == UpdateException.Kind.BAD_FORMAT || e.kind == UpdateException.Kind.SHA_MISMATCH);
        }
        assertFalse(store.ready(12).exists()); // dropped
    }

    @Test
    public void aDifferentSignerIsRefusedWithTheReason() throws Exception {
        byte[] apk = bytes(fx("signed-b.apk"));
        UpdateException e = rejected(apk, apk.length, UpdateException.Kind.SIGNATURE_MISMATCH);
        assertTrue(e.userMessage.contains("署名が今のアプリと違う"));
    }

    @Test
    public void anUnsignedApkIsRefused() throws Exception {
        byte[] apk = bytes(fx("unsigned.apk"));
        rejected(apk, apk.length, UpdateException.Kind.SIGNATURE_UNREADABLE);
    }

    @Test
    public void anArchiveWithoutClassesDexIsRefused() throws Exception {
        byte[] apk = bytes(fx("signed-nodex.apk"));
        rejected(apk, apk.length, UpdateException.Kind.BAD_FORMAT);
    }

    @Test
    public void tamperedOrExtendedContentIsRefused() throws Exception {
        File f = tmp.newFile();
        byte[] other = new byte[30000];
        new Random(1).nextBytes(other);
        UpdatePushFixture.rewrite("signed-a2.apk", f, "classes.dex", other, null);
        byte[] apk = bytes(f);
        UpdateException e = rejected(apk, apk.length, UpdateException.Kind.TAMPERED);
        assertTrue(e.userMessage.contains("書き換え"));
        UpdatePushFixture.rewrite("signed-a2.apk", f, null, null, "evil.dex");
        apk = bytes(f);
        rejected(apk, apk.length, UpdateException.Kind.TAMPERED);
    }

    @Test
    public void theSameOrAnOlderVersionIsRefused() throws Exception {
        byte[] same = bytes(fx("signed-a.apk")); // version 11 = installed
        UpdateException e = rejected(same, same.length, UpdateException.Kind.VERSION_MISMATCH);
        assertTrue(e.userMessage, e.userMessage.contains("同じ版"));
        env.installed = 20;
        byte[] older = bytes(fx("signed-a2.apk"));
        e = rejected(older, older.length, UpdateException.Kind.VERSION_MISMATCH);
        assertTrue(e.userMessage, e.userMessage.contains("ダウングレード"));
    }

    @Test
    public void anotherPackageIsRefused() throws Exception {
        env.pkg = "com.example.other";
        byte[] apk = bytes(fx("signed-a2.apk"));
        rejected(apk, apk.length, UpdateException.Kind.PACKAGE_MISMATCH);
    }

    @Test
    public void notAnApkAtAllIsRefused() throws Exception {
        byte[] text = new byte[40000];
        java.util.Arrays.fill(text, (byte) 'x');
        rejected(text, text.length, UpdateException.Kind.BAD_FORMAT);
        byte[] zipMagicOnly = new byte[40000];
        zipMagicOnly[0] = 'P';
        zipMagicOnly[1] = 'K';
        zipMagicOnly[2] = 3;
        zipMagicOnly[3] = 4;
        rejected(zipMagicOnly, zipMagicOnly.length, UpdateException.Kind.BAD_FORMAT);
    }

    @Test
    public void sizeLimits() throws Exception {
        rejected(new byte[0], 0, UpdateException.Kind.BAD_FORMAT);
        rejected(new byte[100], UpdateEngine.PUSH_MAX_BYTES + 1, UpdateException.Kind.TOO_LARGE);
        rejected(new byte[100], 100, UpdateException.Kind.BAD_FORMAT); // smaller than any APK
    }

    @Test
    public void aCutOffTransferLeavesNothingBehind() throws Exception {
        byte[] apk = bytes(fx("signed-a2.apk"));
        rejected(java.util.Arrays.copyOf(apk, apk.length / 2), apk.length, UpdateException.Kind.NETWORK);
    }

    @Test
    public void aNewPushReplacesTheEarlierOneAndSweepsLeftovers() throws Exception {
        byte[] apk = bytes(fx("signed-a2.apk"));
        engine.receive(new ByteArrayInputStream(apk), apk.length);
        // a crashed upload and a stranger's file are swept at the next one
        new FileOutputStream(store.pushPart()).close();
        new FileOutputStream(new File(store.dir(), "junk.bin")).close();
        env.versionOffset = 1; // the same file now reads as version 13
        UpdateManifest m = engine.receive(new ByteArrayInputStream(apk), apk.length);
        assertEquals(13, m.versionCode);
        assertEquals(java.util.Arrays.asList("update-13.apk", "update-13.meta"), java.util.Arrays.asList(leftovers()));
    }

    // ---- UpdatePush: the code gate and the busy gate ----

    @Test
    public void closedWindowAndWrongCodesNeverReadTheBody() {
        UpdatePush.Result r = push.receive("123456", failingBody(), 100, nowMs);
        assertEquals(403, r.http);
        assertTrue(r.message.contains("受け付け中ではありません"));
        window.open(nowMs);
        r = push.receive("nope", failingBody(), 100, nowMs);
        assertEquals(403, r.http);
        assertTrue(r.message.contains("コードが違います"));
        assertEquals(0, entered.get());
    }

    private static InputStream failingBody() {
        return new InputStream() {
            @Override
            public int read() throws IOException {
                throw new IOException("body must not be read");
            }
        };
    }

    @Test
    public void theCodeIsSingleUse() throws Exception {
        String code = window.open(nowMs);
        byte[] apk = bytes(fx("signed-a2.apk"));
        assertEquals(200, push.receive(code, new ByteArrayInputStream(apk), apk.length, nowMs).http);
        assertFalse(window.isOpen(nowMs));
        assertEquals(403, push.receive(code, new ByteArrayInputStream(apk), apk.length, nowMs).http);
        assertEquals(12, left.versionCode);
    }

    @Test
    public void aRejectedUploadKeepsTheWindowOpenAndReportsNothingReady() throws Exception {
        String code = window.open(nowMs);
        byte[] apk = bytes(fx("signed-b.apk"));
        UpdatePush.Result r = push.receive(code, new ByteArrayInputStream(apk), apk.length, nowMs);
        assertEquals(400, r.http);
        assertFalse(r.ok);
        assertTrue(window.isOpen(nowMs));
        assertNull(left);
        assertEquals(1, entered.get());
    }

    @Test
    public void busyElsewhereIs409() throws Exception {
        String code = window.open(nowMs);
        busyReason = "いま別の更新作業中です";
        byte[] apk = bytes(fx("signed-a2.apk"));
        UpdatePush.Result r = push.receive(code, new ByteArrayInputStream(apk), apk.length, nowMs);
        assertEquals(409, r.http);
        assertTrue(window.isOpen(nowMs)); // the code was not used up
    }

    // ---- over a real socket ----

    private static final class Resp {
        String head;
        String body;

        int status() {
            return Integer.parseInt(head.split(" ")[1]);
        }
    }

    private Resp send(String method, String path, byte[] body, long declaredLength, String... headers) throws IOException {
        Socket s = new Socket("127.0.0.1", target);
        s.setSoTimeout(5000);
        StringBuilder req = new StringBuilder(method + " " + path + " HTTP/1.1\r\nHost: x\r\n");
        for (String h : headers) req.append(h).append("\r\n");
        if (declaredLength >= 0) req.append("Content-Length: ").append(declaredLength).append("\r\n");
        req.append("\r\n");
        OutputStream o = s.getOutputStream();
        o.write(req.toString().getBytes(StandardCharsets.UTF_8));
        if (body != null) {
            try {
                o.write(body);
            } catch (IOException e) {
                // the server may answer before reading everything (oversized upload)
            }
        }
        o.flush();
        InputStream in = s.getInputStream();
        ByteArrayOutputStream all = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        try {
            while ((n = in.read(buf)) > 0) all.write(buf, 0, n);
        } catch (IOException e) {
            if (all.size() == 0) throw e;
        }
        s.close();
        String text = new String(all.toByteArray(), StandardCharsets.UTF_8);
        int split = text.indexOf("\r\n\r\n");
        Resp r = new Resp();
        r.head = text.substring(0, split);
        r.body = text.substring(split + 4);
        return r;
    }

    private Resp upload(byte[] apk, String code) throws IOException {
        return send("POST", HttpServer.PUSH_PATH, apk, apk.length, "X-Z4-Action: update", "X-Z4-Code: " + code,
                "Content-Type: application/vnd.android.package-archive");
    }

    @Test
    public void goodUploadOverTheLanIsAcceptedAndReady() throws Exception {
        String code = window.open(nowMs);
        byte[] apk = bytes(fx("signed-a2.apk"));
        Resp r = upload(apk, code);
        assertEquals(r.body, 200, r.status());
        JSONObject o = new JSONObject(r.body);
        assertTrue(o.getBoolean("ok"));
        assertEquals(ApkCheck.sha256Hex(apk), o.getString("sha256"));
        assertEquals(apk.length, o.getLong("size"));
        assertEquals("1.12", o.getString("version"));
        assertEquals(12, left.versionCode);
        assertEquals(store.ready(12), engine.verifyReady(12));
    }

    @Test
    public void theCustomHeaderIsRequiredAgainstCrossSiteRequests() throws Exception {
        String code = window.open(nowMs);
        byte[] apk = bytes(fx("signed-a2.apk"));
        assertEquals(403, send("POST", HttpServer.PUSH_PATH, apk, apk.length, "X-Z4-Code: " + code).status());
        assertEquals(403, send("POST", HttpServer.PUSH_PATH, apk, apk.length, "X-Z4-Action: delete", "X-Z4-Code: " + code).status());
        assertNull(left);
        assertTrue(window.isOpen(nowMs)); // the code was not used up
    }

    @Test
    public void wrongCodeGetsAJapaneseAnswerEvenWithABigBody() throws Exception {
        window.open(nowMs);
        byte[] apk = bytes(fx("signed-a2.apk"));
        Resp r = upload(apk, "abcdef");
        assertEquals(403, r.status());
        assertTrue(new JSONObject(r.body).getString("message").contains("コードが違います"));
    }

    @Test
    public void closedWindowAnswers403() throws Exception {
        Resp r = upload(bytes(fx("signed-a2.apk")), "123456");
        assertEquals(403, r.status());
        assertTrue(new JSONObject(r.body).getString("message").contains("受け付け中ではありません"));
    }

    @Test
    public void differentSignerGets400WithTheReason() throws Exception {
        String code = window.open(nowMs);
        Resp r = upload(bytes(fx("signed-b.apk")), code);
        assertEquals(400, r.status());
        JSONObject o = new JSONObject(r.body);
        assertFalse(o.getBoolean("ok"));
        assertTrue(o.getString("message").contains("署名が今のアプリと違う"));
    }

    @Test
    public void bodiesLargerThanTheNormalPostLimitAreStreamed() throws Exception {
        String code = window.open(nowMs);
        byte[] big = new byte[400 * 1024]; // over the 256 KB limit of ordinary POST bodies
        new Random(5).nextBytes(big);
        big[0] = 'P';
        big[1] = 'K';
        big[2] = 3;
        big[3] = 4;
        Resp r = upload(big, code);
        assertEquals(r.body, 400, r.status()); // not 413: it was read, then rejected as no APK
        assertTrue(new JSONObject(r.body).getString("message").contains("APK"));
    }

    @Test
    public void ordinaryPostsStillHaveTheSmallBodyLimit() throws Exception {
        byte[] big = new byte[300 * 1024];
        assertEquals(413, send("POST", "/api/settings", big, big.length, "X-Z4-Action: settings").status());
    }

    @Test
    public void declaredSizeOverTheLimitIs413AndMissingLengthIs400() throws Exception {
        String code = window.open(nowMs);
        Resp r = send("POST", HttpServer.PUSH_PATH, new byte[100], UpdateEngine.PUSH_MAX_BYTES + 1,
                "X-Z4-Action: update", "X-Z4-Code: " + code);
        assertEquals(413, r.status());
        assertEquals(400, send("POST", HttpServer.PUSH_PATH, null, -1, "X-Z4-Action: update", "X-Z4-Code: " + code).status());
        assertTrue(window.isOpen(nowMs));
    }

    @Test
    public void notAvailableOverTheInternetEvenWithThePassword() throws Exception {
        String code = window.open(nowMs);
        target = remote.port();
        String basic = "Authorization: Basic " + java.util.Base64.getEncoder().encodeToString("x:correct-horse".getBytes(StandardCharsets.UTF_8));
        byte[] apk = bytes(fx("signed-a2.apk"));
        Resp r = send("POST", HttpServer.PUSH_PATH, apk, apk.length, basic, "X-Z4-Action: update", "X-Z4-Code: " + code);
        assertEquals(403, r.status());
        assertTrue(new JSONObject(r.body).getString("message").contains("外出先"));
        assertNull(left);
        assertTrue(window.isOpen(nowMs));
    }

    @Test
    public void overTheInternetWithoutThePasswordIs401() throws Exception {
        target = remote.port();
        byte[] apk = bytes(fx("signed-a2.apk"));
        assertEquals(401, send("POST", HttpServer.PUSH_PATH, apk, apk.length, "X-Z4-Action: update", "X-Z4-Code: 123456").status());
    }

    @Test
    public void theStatusEndpointIsUnchangedAndOtherMethodsAre405() throws Exception {
        window.open(nowMs);
        Resp r = send("GET", "/api/update", null, -1);
        assertEquals(200, r.status());
        assertTrue(new JSONObject(r.body).getJSONObject("push").getBoolean("open"));
        assertEquals(405, send("PUT", HttpServer.PUSH_PATH, null, -1).status());
        assertNotNull(r.body);
    }
}
