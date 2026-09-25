package app.z4share;

import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Socket;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class Z4HttpServerTest {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final String TOKEN = "0123456789abcdef0123456789abcdef";

    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private File outbox;
    private File inbox;
    private Z4HttpServer server;
    private int port;
    private final List<File> received = new ArrayList<>();

    @Before
    public void setUp() throws IOException {
        outbox = tmp.newFolder("outbox");
        inbox = tmp.newFolder("inbox");
        server = new Z4HttpServer(outbox, inbox, TOKEN, "<html>ok</html>".getBytes(UTF8), "Xperia Z4",
                new Z4HttpServer.Listener() {
                    @Override
                    public void onFileReceived(File file) {
                        synchronized (received) {
                            received.add(file);
                        }
                    }

                    @Override
                    public void onError(String message) {
                    }
                });
        port = server.start(18765);
    }

    @After
    public void tearDown() {
        server.stop();
    }

    // ------------------------------------------------------------ auth

    @Test
    public void rejectsRequestsWithoutToken() throws IOException {
        assertEquals(403, open("/", null).getResponseCode());
        assertEquals(403, open("/api/files", null).getResponseCode());
        assertEquals(403, open("/?k=wrong", null).getResponseCode());
    }

    @Test
    public void indexWithTokenSetsCookieAndCookieWorksAfterwards() throws IOException {
        HttpURLConnection c = open("/?k=" + TOKEN, null);
        assertEquals(200, c.getResponseCode());
        assertEquals("<html>ok</html>", new String(Z4HttpServer.readAll(c.getInputStream()), UTF8));
        String cookie = c.getHeaderField("Set-Cookie");
        assertNotNull(cookie);
        assertTrue(cookie.startsWith("z4k=" + TOKEN + ";"));

        HttpURLConnection c2 = open("/api/files", "z4k=" + TOKEN);
        assertEquals(200, c2.getResponseCode());
    }

    // ------------------------------------------------------------ iPhone -> Z4

    @Test
    public void uploadStoresBodyUnderSanitizedUniqueName() throws IOException {
        byte[] data = randomBytes(3 * 1024 * 1024 + 17);
        assertEquals(200, upload("写真 1.HEIC", data).getResponseCode());
        assertEquals(200, upload("写真 1.HEIC", data).getResponseCode());
        assertEquals(200, upload("../../etc/passwd", "x".getBytes(UTF8)).getResponseCode());

        assertArrayEquals(data, readFile(new File(inbox, "写真 1.HEIC")));
        assertArrayEquals(data, readFile(new File(inbox, "写真 1 (1).HEIC")));
        assertTrue(new File(inbox, "passwd").isFile());
        synchronized (received) {
            assertEquals(3, received.size());
        }
        String[] left = inbox.list();
        assertNotNull(left);
        for (String n : left) {
            assertFalse("temp file left: " + n, n.endsWith(Z4HttpServer.PART_SUFFIX));
        }
    }

    @Test
    public void truncatedUploadIsDiscarded() throws IOException {
        try (Socket s = new Socket("127.0.0.1", port)) {
            OutputStream out = s.getOutputStream();
            out.write(("POST /api/upload?k=" + TOKEN + "&name=cut.bin HTTP/1.1\r\n"
                    + "Host: x\r\nContent-Length: 1000\r\n\r\n").getBytes(UTF8));
            out.write(new byte[10]);
            out.flush();
            s.shutdownOutput();
            String resp = new String(Z4HttpServer.readAll(s.getInputStream()), UTF8);
            assertTrue(resp, resp.startsWith("HTTP/1.1 400"));
        }
        String[] left = inbox.list();
        assertNotNull(left);
        assertEquals(0, left.length);
    }

    @Test
    public void uploadWithoutLengthIsRejected() throws IOException {
        try (Socket s = new Socket("127.0.0.1", port)) {
            s.getOutputStream().write(("POST /api/upload?k=" + TOKEN + "&name=a HTTP/1.1\r\n"
                    + "Host: x\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n").getBytes(UTF8));
            String resp = new String(Z4HttpServer.readAll(s.getInputStream()), UTF8);
            assertTrue(resp, resp.startsWith("HTTP/1.1 411"));
        }
    }

    // ------------------------------------------------------------ Z4 -> iPhone

    @Test
    public void listsAndDownloadsOutboxFiles() throws IOException {
        byte[] data = randomBytes(200_000);
        writeFile(new File(outbox, "動画 #1.mp4"), data);
        writeFile(new File(inbox, "from-iphone.jpg"), new byte[]{1, 2, 3});

        HttpURLConnection list = open("/api/files?k=" + TOKEN, null);
        String json = new String(Z4HttpServer.readAll(list.getInputStream()), UTF8);
        assertTrue(json, json.contains("\"device\":\"Xperia Z4\""));
        assertTrue(json, json.contains("\"name\":\"動画 #1.mp4\""));
        assertTrue(json, json.contains("\"url\":\"/dl/%E5%8B%95%E7%94%BB%20%231.mp4\""));
        assertTrue(json, json.contains("\"name\":\"from-iphone.jpg\",\"size\":3"));

        HttpURLConnection dl = open("/dl/" + Z4HttpServer.urlEncodePath("動画 #1.mp4") + "?k=" + TOKEN, null);
        assertEquals(200, dl.getResponseCode());
        assertEquals("video/mp4", dl.getContentType());
        assertTrue(dl.getHeaderField("Content-Disposition").startsWith("attachment;"));
        assertArrayEquals(data, Z4HttpServer.readAll(dl.getInputStream()));

        HttpURLConnection part = open("/dl/" + Z4HttpServer.urlEncodePath("動画 #1.mp4")
                + "?k=" + TOKEN + "&inline=1", null);
        part.setRequestProperty("Range", "bytes=100-199");
        assertEquals(206, part.getResponseCode());
        assertEquals("bytes 100-199/200000", part.getHeaderField("Content-Range"));
        assertTrue(part.getHeaderField("Content-Disposition").startsWith("inline;"));
        byte[] expected = new byte[100];
        System.arraycopy(data, 100, expected, 0, 100);
        assertArrayEquals(expected, Z4HttpServer.readAll(part.getInputStream()));

        HttpURLConnection bad = open("/dl/" + Z4HttpServer.urlEncodePath("動画 #1.mp4") + "?k=" + TOKEN, null);
        bad.setRequestProperty("Range", "bytes=999999-");
        assertEquals(416, bad.getResponseCode());
    }

    @Test
    public void downloadCannotEscapeOutbox() throws IOException {
        writeFile(new File(inbox, "secret.txt"), "s".getBytes(UTF8));
        assertEquals(404, open("/dl/..%2Finbox%2Fsecret.txt?k=" + TOKEN, null).getResponseCode());
        assertEquals(404, open("/dl/..?k=" + TOKEN, null).getResponseCode());
        assertEquals(404, open("/dl/nothing.txt?k=" + TOKEN, null).getResponseCode());
    }

    // ------------------------------------------------------------ helpers under test

    @Test
    public void sanitizeFileName() {
        assertEquals("a.jpg", Z4HttpServer.sanitizeFileName("a.jpg"));
        assertEquals("b.txt", Z4HttpServer.sanitizeFileName("..\\..\\b.txt"));
        assertEquals("hidden", Z4HttpServer.sanitizeFileName("...hidden"));
        assertEquals("a_b_.png", Z4HttpServer.sanitizeFileName("a:b?.png"));
        assertEquals("file", Z4HttpServer.sanitizeFileName(""));
        assertEquals("file", Z4HttpServer.sanitizeFileName(null));
        assertEquals("file", Z4HttpServer.sanitizeFileName(".."));
        StringBuilder longName = new StringBuilder();
        for (int i = 0; i < 300; i++) {
            longName.append('あ');
        }
        String s = Z4HttpServer.sanitizeFileName(longName + ".jpeg");
        assertEquals(120, s.length());
        assertTrue(s.endsWith(".jpeg"));
    }

    @Test
    public void parseRange() {
        assertArrayEquals(new long[]{0, 9}, Z4HttpServer.parseRange("bytes=0-9", 100));
        assertArrayEquals(new long[]{90, 99}, Z4HttpServer.parseRange("bytes=90-", 100));
        assertArrayEquals(new long[]{80, 99}, Z4HttpServer.parseRange("bytes=-20", 100));
        assertArrayEquals(new long[]{50, 99}, Z4HttpServer.parseRange("bytes=50-500", 100));
        assertNull(Z4HttpServer.parseRange("bytes=100-", 100));
        assertNull(Z4HttpServer.parseRange("bytes=0-1,5-6", 100));
        assertNull(Z4HttpServer.parseRange("items=0-1", 100));
        assertNull(Z4HttpServer.parseRange("bytes=5-2", 100));
    }

    // ------------------------------------------------------------ plumbing

    private HttpURLConnection open(String path, String cookie) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:" + port + path).openConnection();
        c.setUseCaches(false);
        if (cookie != null) {
            c.setRequestProperty("Cookie", cookie);
        }
        return c;
    }

    private HttpURLConnection upload(String name, byte[] body) throws IOException {
        HttpURLConnection c = open("/api/upload?k=" + TOKEN + "&name=" + URLEncoder.encode(name, "UTF-8"), null);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setFixedLengthStreamingMode(body.length);
        try (OutputStream out = c.getOutputStream()) {
            out.write(body);
        }
        c.getResponseCode();
        return c;
    }

    private static byte[] randomBytes(int n) {
        byte[] b = new byte[n];
        new Random(n).nextBytes(b);
        return b;
    }

    private static void writeFile(File f, byte[] data) throws IOException {
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(data);
        }
    }

    private static byte[] readFile(File f) throws IOException {
        try (InputStream in = new java.io.FileInputStream(f)) {
            return Z4HttpServer.readAll(in);
        }
    }
}
