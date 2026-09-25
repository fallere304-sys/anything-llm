package com.z4motioncam;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class HttpServerTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private File dir;
    private HttpServer server;
    private FrameHub hub;
    private String password = "";
    private byte[] video;

    @Before
    public void setUp() throws IOException {
        dir = tmp.newFolder("rec");
        video = new byte[1000];
        for (int i = 0; i < video.length; i++) video[i] = (byte) i;
        FileOutputStream out = new FileOutputStream(new File(dir, "20260101_120000.mp4"));
        out.write(video);
        out.close();
        final RecordingStore store = new RecordingStore(dir, 0);
        hub = new FrameHub();
        server = new HttpServer(0, new HttpServer.Backend() {
            @Override public byte[] indexHtml() { return "<html>hi</html>".getBytes(StandardCharsets.UTF_8); }
            @Override public String statusJson() { return "{\"state\":\"watching\"}"; }
            @Override public RecordingStore store() { return store; }
            @Override public FrameHub frames() { return hub; }
            @Override public String password() { return password; }
        });
        server.start();
    }

    @After
    public void tearDown() {
        hub.close();
        server.stop();
    }

    private static final class Resp {
        String head;
        byte[] body;
    }

    private Resp get(String path, String... headers) throws IOException {
        return request("GET", path, null, headers);
    }

    private Resp post(String path, String body, String... headers) throws IOException {
        return request("POST", path, body, headers);
    }

    private Resp request(String method, String path, String body, String... headers) throws IOException {
        Socket s = new Socket("127.0.0.1", server.port());
        s.setSoTimeout(5000);
        byte[] bodyBytes = body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8);
        StringBuilder req = new StringBuilder(method + " " + path + " HTTP/1.1\r\nHost: x\r\n");
        for (String h : headers) req.append(h).append("\r\n");
        if (body != null) req.append("Content-Length: ").append(bodyBytes.length).append("\r\n");
        req.append("\r\n");
        OutputStream o = s.getOutputStream();
        o.write(req.toString().getBytes(StandardCharsets.UTF_8));
        o.write(bodyBytes);
        o.flush();
        InputStream in = s.getInputStream();
        ByteArrayOutputStream all = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) all.write(buf, 0, n);
        s.close();
        byte[] raw = all.toByteArray();
        String text = new String(raw, StandardCharsets.ISO_8859_1);
        int split = text.indexOf("\r\n\r\n");
        Resp r = new Resp();
        r.head = text.substring(0, split);
        r.body = java.util.Arrays.copyOfRange(raw, split + 4, raw.length);
        return r;
    }

    @Test
    public void servesPageStatusAndList() throws IOException {
        assertTrue(get("/").head.startsWith("HTTP/1.0 200"));
        assertEquals("{\"state\":\"watching\"}", new String(get("/api/status").body, StandardCharsets.UTF_8));
        String list = new String(get("/api/recordings").body, StandardCharsets.UTF_8);
        assertTrue(list, list.startsWith("[{\"name\":\"20260101_120000.mp4\",\"size\":1000"));
        assertTrue(get("/nope").head.startsWith("HTTP/1.0 404"));
        assertTrue(get("/rec/..%2F..%2Fetc%2Fpasswd").head.startsWith("HTTP/1.0 404"));
    }

    @Test
    public void servesVideoWithRanges() throws IOException {
        Resp full = get("/rec/20260101_120000.mp4");
        assertTrue(full.head.startsWith("HTTP/1.0 200"));
        assertTrue(full.head.contains("Accept-Ranges: bytes"));
        assertArrayEquals(video, full.body);

        Resp part = get("/rec/20260101_120000.mp4", "Range: bytes=100-199");
        assertTrue(part.head, part.head.startsWith("HTTP/1.0 206"));
        assertTrue(part.head.contains("Content-Range: bytes 100-199/1000"));
        assertArrayEquals(java.util.Arrays.copyOfRange(video, 100, 200), part.body);

        Resp tail = get("/rec/20260101_120000.mp4", "Range: bytes=-10");
        assertTrue(tail.head.contains("Content-Range: bytes 990-999/1000"));
        assertEquals(10, tail.body.length);

        assertTrue(get("/rec/20260101_120000.mp4", "Range: bytes=5000-").head.startsWith("HTTP/1.0 416"));
    }

    @Test
    public void rangeParsing() {
        assertArrayEquals(new long[] {0, 999, 0}, HttpServer.parseRange(null, 1000));
        assertArrayEquals(new long[] {0, 999, 1}, HttpServer.parseRange("bytes=0-", 1000));
        assertArrayEquals(new long[] {500, 999, 1}, HttpServer.parseRange("bytes=500-5000", 1000));
        assertArrayEquals(new long[] {0, 999, 1}, HttpServer.parseRange("bytes=-5000", 1000));
        assertNull(HttpServer.parseRange("bytes=1000-", 1000));
        assertNull(HttpServer.parseRange("bytes=10-5", 1000));
        assertArrayEquals(new long[] {0, 999, 0}, HttpServer.parseRange("bytes=x-y", 1000));
    }

    @Test
    public void passwordProtection() throws IOException {
        password = "secret";
        Resp denied = get("/api/status");
        assertTrue(denied.head.startsWith("HTTP/1.0 401"));
        assertTrue(denied.head.contains("WWW-Authenticate: Basic"));
        // "papa:secret"
        assertTrue(get("/api/status", "Authorization: Basic cGFwYTpzZWNyZXQ=").head.startsWith("HTTP/1.0 200"));
        // "papa:wrong"
        assertTrue(get("/api/status", "Authorization: Basic cGFwYTp3cm9uZw==").head.startsWith("HTTP/1.0 401"));
        assertFalse(HttpServer.checkBasicAuth("Basic !!!", "secret"));
        assertFalse(HttpServer.checkBasicAuth("Basic OnNlY3JldHg=", "secret")); // ":secretx"
        assertTrue(HttpServer.checkBasicAuth("basic OnNlY3JldA==", "secret")); // ":secret"
    }

    @Test
    public void mjpegStreamsFramesOnlyWhileWatched() throws Exception {
        assertFalse(hub.isWanted(System.currentTimeMillis()));
        final Socket s = new Socket("127.0.0.1", server.port());
        s.setSoTimeout(5000);
        s.getOutputStream().write("GET /stream.mjpg HTTP/1.1\r\n\r\n".getBytes(StandardCharsets.UTF_8));
        InputStream in = s.getInputStream();
        // Wait until the server registered the viewer.
        for (int i = 0; i < 100 && hub.clients() == 0; i++) Thread.sleep(20);
        assertTrue(hub.isWanted(System.currentTimeMillis()));
        hub.publish(new byte[] {(byte) 0xFF, (byte) 0xD8, 1, 2, 3});
        ByteArrayOutputStream got = new ByteArrayOutputStream();
        byte[] buf = new byte[1024];
        while (!new String(got.toByteArray(), StandardCharsets.ISO_8859_1).contains("Content-Length: 5\r\n\r\n")) {
            int n = in.read(buf);
            if (n < 0) break;
            got.write(buf, 0, n);
        }
        String text = new String(got.toByteArray(), StandardCharsets.ISO_8859_1);
        assertTrue(text, text.contains("multipart/x-mixed-replace; boundary=frame"));
        assertTrue(text.contains("--frame\r\nContent-Type: image/jpeg"));
        s.close();
        // Viewer gone -> the next write fails and the client slot is released.
        for (int i = 0; i < 100 && hub.clients() > 0; i++) {
            hub.publish(new byte[] {1});
            Thread.sleep(20);
        }
        assertEquals(0, hub.clients());
    }

    @Test
    public void snapshotRequestsAFrame() throws Exception {
        Thread producer = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    for (int i = 0; i < 100 && !hub.isWanted(System.currentTimeMillis()); i++) Thread.sleep(20);
                    hub.publish(new byte[] {9, 9});
                } catch (InterruptedException ignored) {
                    // test ends
                }
            }
        });
        producer.start();
        Resp r = get("/snapshot.jpg");
        producer.join();
        assertTrue(r.head.startsWith("HTTP/1.0 200"));
        assertArrayEquals(new byte[] {9, 9}, r.body);
    }

    private void addRecording(String name, int size) throws IOException {
        FileOutputStream out = new FileOutputStream(new File(dir, name));
        out.write(new byte[size]);
        out.close();
    }

    @Test
    public void deletesSelectedRecordings() throws IOException {
        addRecording("20260102_120000.mp4", 10);
        addRecording("20260103_120000.mp4", 10);
        addRecording("20260104_120000.mp4.part", 10);
        Resp r = post("/api/delete", "20260101_120000.mp4\n20260102_120000.mp4\n../x.mp4\n20260104_120000.mp4.part",
                "X-Requested-With: z4motioncam");
        assertTrue(r.head, r.head.startsWith("HTTP/1.0 200"));
        assertEquals("{\"deleted\":[\"20260101_120000.mp4\",\"20260102_120000.mp4\"],"
                + "\"failed\":[\"../x.mp4\",\"20260104_120000.mp4.part\"]}",
                new String(r.body, StandardCharsets.UTF_8));
        assertFalse(new File(dir, "20260101_120000.mp4").exists());
        assertTrue(new File(dir, "20260103_120000.mp4").exists());
        assertTrue("recording in progress is never deleted", new File(dir, "20260104_120000.mp4.part").exists());
    }

    @Test
    public void deleteRequiresCustomHeader() throws IOException {
        // A cross-site form post cannot set this header, so it cannot delete anything.
        Resp r = post("/api/delete", "20260101_120000.mp4");
        assertTrue(r.head.startsWith("HTTP/1.0 403"));
        assertTrue(new File(dir, "20260101_120000.mp4").exists());
    }

    @Test
    public void deleteRequiresPasswordWhenSet() throws IOException {
        password = "secret";
        Resp r = post("/api/delete", "20260101_120000.mp4", "X-Requested-With: z4motioncam");
        assertTrue(r.head.startsWith("HTTP/1.0 401"));
        assertTrue(new File(dir, "20260101_120000.mp4").exists());
    }

    @Test
    public void zipsSelectedRecordings() throws IOException {
        addRecording("20260102_120000.mp4", 300);
        addRecording("20260103_120000.mp4", 5);
        // Browser form encoding: newlines become %0D%0A.
        Resp r = post("/api/zip", "names=20260101_120000.mp4%0D%0A20260102_120000.mp4%0D%0Anope.mp4",
                "Content-Type: application/x-www-form-urlencoded");
        assertTrue(r.head, r.head.startsWith("HTTP/1.0 200"));
        assertTrue(r.head.contains("Content-Type: application/zip"));
        assertTrue(r.head.contains("Content-Disposition: attachment; filename=\"z4motioncam_"));
        java.util.zip.ZipInputStream zin = new java.util.zip.ZipInputStream(new java.io.ByteArrayInputStream(r.body));
        java.util.zip.ZipEntry e1 = zin.getNextEntry();
        assertEquals("20260101_120000.mp4", e1.getName());
        ByteArrayOutputStream content = new ByteArrayOutputStream();
        byte[] buf = new byte[512];
        int n;
        while ((n = zin.read(buf)) > 0) content.write(buf, 0, n);
        assertArrayEquals(video, content.toByteArray());
        assertEquals("20260102_120000.mp4", zin.getNextEntry().getName());
        assertNull(zin.getNextEntry());
    }

    @Test
    public void zipWithoutValidNamesIsRejected() throws IOException {
        assertTrue(post("/api/zip", "names=..%2Fsecret").head.startsWith("HTTP/1.0 400"));
    }

    @Test
    public void oversizedBodyIsRejected() throws IOException {
        StringBuilder big = new StringBuilder();
        while (big.length() < 300 * 1024) big.append("20260101_120000.mp4\n");
        assertTrue(post("/api/delete", big.toString(), "X-Requested-With: z4motioncam").head.startsWith("HTTP/1.0 413"));
        assertTrue(new File(dir, "20260101_120000.mp4").exists());
    }

    @Test
    public void formAndNameParsing() {
        assertEquals("a b\nc", HttpServer.parseForm("names=a+b&names=c&x").get("names"));
        assertEquals("", HttpServer.parseForm("names=a+b&names=c&x").get("x"));
        assertEquals(java.util.Arrays.asList("a", "b", "c"), HttpServer.splitNames(" a\r\nb,,c\n"));
    }
}
