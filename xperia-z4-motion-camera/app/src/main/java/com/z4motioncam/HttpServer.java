package com.z4motioncam;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.net.URLDecoder;
import java.nio.charset.Charset;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.zip.Deflater;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import javax.net.ServerSocketFactory;
import javax.net.ssl.SSLServerSocket;

/**
 * Minimal HTTP/1.0 server for the LAN: web page, MJPEG live view, recordings with Range support.
 * Plain java.net so it has no dependencies; one short-lived thread per connection, capped.
 */
final class HttpServer {
    interface Backend {
        byte[] indexHtml();

        String statusJson();

        RecordingStore store();

        FrameHub frames();

        /** Password for the Internet-facing (HTTPS) server. The LAN server never asks for one. */
        String password();
    }

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int MAX_CONNECTIONS = 8;
    private static final int MAX_STREAMS = 3;
    private static final int READ_TIMEOUT_MS = 15_000;
    private static final int MAX_BODY_BYTES = 256 * 1024;
    private static final long MAX_ZIP_BYTES = 3_900_000_000L;

    private static final int MAX_AUTH_FAILURES = 5;
    private static final long AUTH_WINDOW_MS = 10 * 60_000L;
    private static final long AUTH_BLOCK_MS = 15 * 60_000L;

    private final int port;
    private final Backend backend;
    private final ServerSocketFactory factory;
    /** LAN server: refuse connections that do not come from the home network. */
    private final boolean localOnly;
    /**
     * Internet-facing server: every request needs the password, and nothing is served while none
     * is set. The LAN server (false) never asks for a password.
     */
    private final boolean requirePassword;
    /** ip -> {failures, windowStart, blockedUntil} */
    private final Map<String, long[]> authFailures = new HashMap<>();
    private final Set<Socket> sockets = new HashSet<>();
    private ServerSocket server;
    private ThreadPoolExecutor pool;
    private Thread acceptThread;
    private volatile boolean running;
    private int streams;

    HttpServer(int port, Backend backend) {
        this(port, backend, null, false, false);
    }

    HttpServer(int port, Backend backend, ServerSocketFactory factory, boolean localOnly, boolean requirePassword) {
        this.port = port;
        this.backend = backend;
        this.factory = factory;
        this.localOnly = localOnly;
        this.requirePassword = requirePassword;
    }

    int port() {
        return server != null ? server.getLocalPort() : port;
    }

    void start() throws IOException {
        server = factory == null ? new ServerSocket() : factory.createServerSocket();
        if (server instanceof SSLServerSocket) {
            // TLS 1.2+ only (Android 5-7 would otherwise also offer TLS 1.0/1.1).
            SSLServerSocket ssl = (SSLServerSocket) server;
            List<String> modern = new ArrayList<>();
            for (String p : ssl.getSupportedProtocols()) {
                if (p.equals("TLSv1.2") || p.equals("TLSv1.3")) modern.add(p);
            }
            if (!modern.isEmpty()) ssl.setEnabledProtocols(modern.toArray(new String[0]));
        }
        server.setReuseAddress(true);
        server.bind(new InetSocketAddress(port));
        pool = new ThreadPoolExecutor(0, MAX_CONNECTIONS, 30, TimeUnit.SECONDS,
                new SynchronousQueue<Runnable>());
        running = true;
        acceptThread = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "http-accept");
        acceptThread.start();
    }

    void stop() {
        running = false;
        closeQuietly(server);
        synchronized (sockets) {
            for (Socket s : sockets) closeQuietly(s);
            sockets.clear();
        }
        if (pool != null) pool.shutdownNow();
        if (acceptThread != null) {
            try {
                acceptThread.join(2000);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private void acceptLoop() {
        while (running) {
            final Socket s;
            try {
                s = server.accept();
            } catch (IOException e) {
                if (running) continue;
                return;
            }
            synchronized (sockets) {
                sockets.add(s);
            }
            try {
                pool.execute(new Runnable() {
                    @Override
                    public void run() {
                        serve(s);
                    }
                });
            } catch (RuntimeException rejected) {
                try {
                    writeSimple(s.getOutputStream(), 503, "text/plain", "busy\n".getBytes(UTF8), false);
                } catch (IOException ignored) {
                    // client gone
                }
                release(s);
            }
        }
    }

    private void release(Socket s) {
        closeQuietly(s);
        synchronized (sockets) {
            sockets.remove(s);
        }
    }

    private void serve(Socket s) {
        try {
            if (localOnly && !NetUtil.isLocalAddress(s.getInetAddress())) return;
            s.setSoTimeout(READ_TIMEOUT_MS);
            s.setTcpNoDelay(true);
            InputStream in = new BufferedInputStream(s.getInputStream());
            OutputStream out = s.getOutputStream();
            Request req = Request.read(in);
            if (req == null) return;
            handle(req, out, s.getInetAddress().getHostAddress());
            out.flush();
        } catch (IOException ignored) {
            // client disconnected or timed out
        } finally {
            release(s);
        }
    }

    private void handle(Request req, OutputStream out, String clientIp) throws IOException {
        boolean head = "HEAD".equals(req.method);
        if (requirePassword) {
            String pw = backend.password();
            if (pw == null || pw.isEmpty()) {
                writeSimple(out, 403, "text/plain; charset=utf-8",
                        "パスワードが未設定のため外部からの接続を停止しています\n".getBytes(UTF8), head);
                return;
            }
            if (isBlocked(clientIp)) {
                writeSimple(out, 429, "text/plain; charset=utf-8",
                        "パスワードの誤りが続いたため一時的にブロックしています。15分後に再試行してください\n".getBytes(UTF8), head);
                return;
            }
            String auth = req.headers.get("authorization");
            if (!checkBasicAuth(auth, pw)) {
                if (auth != null) recordAuthFailure(clientIp);
                String h = "HTTP/1.0 401 Unauthorized\r\n"
                        + "WWW-Authenticate: Basic realm=\"Z4 MotionCam\", charset=\"UTF-8\"\r\n"
                        + "Content-Length: 0\r\nConnection: close\r\n\r\n";
                out.write(h.getBytes(UTF8));
                return;
            }
            clearAuthFailures(clientIp);
        }
        if ("POST".equals(req.method)) {
            handlePost(req, out);
            return;
        }
        if (!"GET".equals(req.method) && !head) {
            writeSimple(out, 405, "text/plain", "method not allowed\n".getBytes(UTF8), head);
            return;
        }

        String path = req.path;
        if (path.equals("/") || path.equals("/index.html")) {
            writeSimple(out, 200, "text/html; charset=utf-8", backend.indexHtml(), head);
        } else if (path.equals("/api/status")) {
            writeSimple(out, 200, "application/json; charset=utf-8",
                    backend.statusJson().getBytes(UTF8), head);
        } else if (path.equals("/api/recordings")) {
            writeSimple(out, 200, "application/json; charset=utf-8",
                    recordingsJson(backend.store().list()).getBytes(UTF8), head);
        } else if (path.equals("/snapshot.jpg")) {
            serveSnapshot(out, head);
        } else if (path.equals("/stream.mjpg")) {
            serveMjpeg(out, head);
        } else if (path.startsWith("/rec/")) {
            File f = backend.store().find(path.substring("/rec/".length()));
            if (f == null) {
                writeSimple(out, 404, "text/plain", "not found\n".getBytes(UTF8), head);
            } else {
                serveFile(out, f, req.headers.get("range"), "download".equals(req.query.get("dl")), head);
            }
        } else {
            writeSimple(out, 404, "text/plain", "not found\n".getBytes(UTF8), head);
        }
    }

    private void handlePost(Request req, OutputStream out) throws IOException {
        if (req.body == null) {
            writeSimple(out, 413, "text/plain", "request too large\n".getBytes(UTF8), false);
        } else if (req.path.equals("/api/delete")) {
            // A custom header cannot be sent cross-origin without a CORS preflight, which this server
            // never approves, so other web pages cannot delete recordings through the viewer's browser.
            // (Not X-Requested-With: Android WebView-based browsers overwrite that one.)
            if (!"delete".equals(req.headers.get("x-z4-action"))) {
                writeSimple(out, 403, "text/plain", "forbidden\n".getBytes(UTF8), false);
                return;
            }
            writeSimple(out, 200, "application/json; charset=utf-8",
                    deleteRecordings(splitNames(new String(req.body, UTF8))).getBytes(UTF8), false);
        } else if (req.path.equals("/api/zip")) {
            List<String> names = new ArrayList<>();
            for (String v : parseForm(new String(req.body, UTF8)).values()) names.addAll(splitNames(v));
            serveZip(out, names);
        } else {
            writeSimple(out, 404, "text/plain", "not found\n".getBytes(UTF8), false);
        }
    }

    private boolean isBlocked(String ip) {
        synchronized (authFailures) {
            long[] f = authFailures.get(ip);
            return f != null && f[2] > System.currentTimeMillis();
        }
    }

    private void recordAuthFailure(String ip) {
        long now = System.currentTimeMillis();
        synchronized (authFailures) {
            if (authFailures.size() > 1000) authFailures.clear(); // bound memory under a flood
            long[] f = authFailures.get(ip);
            if (f == null || now - f[1] > AUTH_WINDOW_MS) {
                f = new long[] {0, now, 0};
                authFailures.put(ip, f);
            }
            if (++f[0] >= MAX_AUTH_FAILURES) f[2] = now + AUTH_BLOCK_MS;
        }
    }

    private void clearAuthFailures(String ip) {
        synchronized (authFailures) {
            authFailures.remove(ip);
        }
    }

    /** Deletes finished recordings by name; returns {"deleted":[...],"failed":[...]}. */
    private String deleteRecordings(List<String> names) {
        StringBuilder deleted = new StringBuilder();
        StringBuilder failed = new StringBuilder();
        for (String name : names) {
            File f = backend.store().find(name);
            StringBuilder target = f != null && f.delete() ? deleted : failed;
            if (target.length() > 0) target.append(',');
            target.append(jsonString(name));
        }
        return "{\"deleted\":[" + deleted + "],\"failed\":[" + failed + "]}";
    }

    /**
     * Streams the selected recordings as one ZIP. Entries are stored without compression (MP4 is
     * already compressed), so this costs almost no CPU on the phone.
     */
    private void serveZip(OutputStream out, List<String> names) throws IOException {
        List<File> files = new ArrayList<>();
        long total = 0;
        for (String n : names) {
            File f = backend.store().find(n);
            if (f != null && !files.contains(f)) {
                files.add(f);
                total += f.length();
            }
        }
        if (files.isEmpty()) {
            writeSimple(out, 400, "text/plain; charset=utf-8", "ファイルが選択されていません\n".getBytes(UTF8), false);
            return;
        }
        if (total > MAX_ZIP_BYTES) {
            // Android before 7.0 cannot write ZIP64, so a ZIP must stay under 4 GB.
            writeSimple(out, 413, "text/plain; charset=utf-8",
                    "選択したファイルの合計が大きすぎます（3.9GBまで）\n".getBytes(UTF8), false);
            return;
        }
        String zipName = "z4motioncam_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".zip";
        out.write(("HTTP/1.0 200 OK\r\nContent-Type: application/zip\r\n"
                + "Content-Disposition: attachment; filename=\"" + zipName + "\"\r\n"
                + "Cache-Control: no-cache\r\nConnection: close\r\n\r\n").getBytes(UTF8));
        ZipOutputStream zip = new ZipOutputStream(new BufferedOutputStream(out, 64 * 1024));
        zip.setLevel(Deflater.NO_COMPRESSION);
        byte[] buf = new byte[64 * 1024];
        for (File f : files) {
            InputStream in;
            try {
                in = new FileInputStream(f);
            } catch (IOException gone) {
                continue; // deleted meanwhile (e.g. by the storage rotation)
            }
            try {
                ZipEntry e = new ZipEntry(f.getName());
                e.setTime(f.lastModified());
                zip.putNextEntry(e);
                int n;
                while ((n = in.read(buf)) > 0) zip.write(buf, 0, n);
                zip.closeEntry();
            } finally {
                in.close();
            }
        }
        zip.finish();
        zip.flush();
    }

    /** Splits a newline / comma separated list of names. */
    static List<String> splitNames(String s) {
        List<String> out = new ArrayList<>();
        for (String n : s.split("[\\r\\n,]+")) {
            String t = n.trim();
            if (!t.isEmpty()) out.add(t);
        }
        return out;
    }

    /** Parses an application/x-www-form-urlencoded body (repeated keys are joined with newlines). */
    static Map<String, String> parseForm(String body) {
        Map<String, String> m = new HashMap<>();
        for (String kv : body.split("&")) {
            if (kv.isEmpty()) continue;
            int e = kv.indexOf('=');
            String k = Request.decode(e >= 0 ? kv.substring(0, e) : kv);
            String v = e >= 0 ? Request.decode(kv.substring(e + 1)) : "";
            String prev = m.get(k);
            m.put(k, prev == null ? v : prev + "\n" + v);
        }
        return m;
    }

    static String jsonString(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') sb.append('\\').append(c);
            else if (c < 0x20) sb.append(String.format(Locale.US, "\\u%04x", (int) c));
            else sb.append(c);
        }
        return sb.append('"').toString();
    }

    private void serveSnapshot(OutputStream out, boolean head) throws IOException {
        FrameHub hub = backend.frames();
        hub.request(System.currentTimeMillis());
        FrameHub.Frame f;
        try {
            FrameHub.Frame cached = hub.latest();
            f = hub.await(cached == null ? -1 : cached.seq, 3000);
        } catch (InterruptedException e) {
            return;
        }
        if (f == null) {
            writeSimple(out, 503, "text/plain", "camera not ready\n".getBytes(UTF8), head);
        } else {
            writeSimple(out, 200, "image/jpeg", f.jpeg, head);
        }
    }

    private void serveMjpeg(OutputStream out, boolean head) throws IOException {
        synchronized (this) {
            if (streams >= MAX_STREAMS) {
                writeSimple(out, 503, "text/plain", "too many viewers\n".getBytes(UTF8), head);
                return;
            }
            streams++;
        }
        FrameHub hub = backend.frames();
        hub.addClient();
        try {
            out.write(("HTTP/1.0 200 OK\r\n"
                    + "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                    + "Cache-Control: no-cache, no-store\r\nPragma: no-cache\r\n"
                    + "Connection: close\r\n\r\n").getBytes(UTF8));
            out.flush();
            if (head) return;
            long seq = -1;
            while (running) {
                FrameHub.Frame f = hub.await(seq, 5000);
                if (f == null) {
                    if (!running) break;
                    continue;
                }
                seq = f.seq;
                out.write(("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + f.jpeg.length + "\r\n\r\n").getBytes(UTF8));
                out.write(f.jpeg);
                out.write("\r\n".getBytes(UTF8));
                out.flush();
            }
        } catch (InterruptedException ignored) {
            // shutting down
        } finally {
            hub.removeClient();
            synchronized (this) {
                streams--;
            }
        }
    }

    private static void serveFile(OutputStream out, File f, String range, boolean download,
                                  boolean head) throws IOException {
        long len = f.length();
        long[] r = parseRange(range, len);
        StringBuilder h = new StringBuilder();
        if (r == null) {
            h.append("HTTP/1.0 416 Range Not Satisfiable\r\nContent-Range: bytes */").append(len)
                    .append("\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            out.write(h.toString().getBytes(UTF8));
            return;
        }
        long start = r[0];
        long end = r[1];
        boolean partial = r[2] == 1;
        long count = len == 0 ? 0 : end - start + 1;
        h.append(partial ? "HTTP/1.0 206 Partial Content\r\n" : "HTTP/1.0 200 OK\r\n")
                .append("Content-Type: video/mp4\r\n")
                .append("Accept-Ranges: bytes\r\n")
                .append("Content-Length: ").append(count).append("\r\n");
        if (partial) {
            h.append("Content-Range: bytes ").append(start).append('-').append(end).append('/')
                    .append(len).append("\r\n");
        }
        if (download) {
            h.append("Content-Disposition: attachment; filename=\"").append(f.getName()).append("\"\r\n");
        }
        h.append("Connection: close\r\n\r\n");
        out.write(h.toString().getBytes(UTF8));
        if (head || count == 0) return;

        RandomAccessFile raf = new RandomAccessFile(f, "r");
        try {
            raf.seek(start);
            byte[] buf = new byte[64 * 1024];
            long left = count;
            while (left > 0) {
                int n = raf.read(buf, 0, (int) Math.min(buf.length, left));
                if (n < 0) break;
                out.write(buf, 0, n);
                left -= n;
            }
        } finally {
            raf.close();
        }
    }

    /**
     * Parses a single {@code bytes=} range. Returns {start, end, explicit} where explicit is 1 if a
     * valid range header was given; the whole file when absent or unparseable; null if unsatisfiable.
     */
    static long[] parseRange(String header, long len) {
        long[] whole = {0, Math.max(0, len - 1), 0};
        if (header == null) return whole;
        String h = header.trim().toLowerCase(Locale.US);
        if (!h.startsWith("bytes=") || h.indexOf(',') >= 0) return whole;
        String spec = h.substring(6).trim();
        int dash = spec.indexOf('-');
        if (dash < 0) return whole;
        try {
            String a = spec.substring(0, dash).trim();
            String b = spec.substring(dash + 1).trim();
            long start;
            long end;
            if (a.isEmpty()) {
                if (b.isEmpty()) return whole;
                long suffix = Long.parseLong(b);
                if (suffix <= 0) return null;
                start = Math.max(0, len - suffix);
                end = len - 1;
            } else {
                start = Long.parseLong(a);
                end = b.isEmpty() ? len - 1 : Math.min(Long.parseLong(b), len - 1);
            }
            if (start >= len || start > end) return null;
            return new long[] {start, end, 1};
        } catch (NumberFormatException e) {
            return whole;
        }
    }

    static boolean checkBasicAuth(String header, String password) {
        if (header == null) return false;
        String h = header.trim();
        if (!h.regionMatches(true, 0, "Basic ", 0, 6)) return false;
        byte[] decoded = Base64.decode(h.substring(6).trim());
        if (decoded == null) return false;
        String cred = new String(decoded, UTF8);
        int colon = cred.indexOf(':');
        if (colon < 0) return false;
        // Any user name is accepted; only the password matters. Constant-time compare.
        byte[] given = cred.substring(colon + 1).getBytes(UTF8);
        byte[] expected = password.getBytes(UTF8);
        int diff = given.length ^ expected.length;
        for (int i = 0; i < given.length; i++) {
            diff |= given[i] ^ expected[i % Math.max(1, expected.length)];
        }
        return diff == 0 && expected.length > 0;
    }

    static String recordingsJson(List<File> files) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < files.size(); i++) {
            File f = files.get(i);
            if (i > 0) sb.append(',');
            sb.append("{\"name\":\"").append(f.getName()).append("\",\"size\":").append(f.length())
                    .append(",\"modified\":").append(f.lastModified()).append('}');
        }
        return sb.append(']').toString();
    }

    private static void writeSimple(OutputStream out, int code, String type, byte[] body,
                                    boolean head) throws IOException {
        String h = "HTTP/1.0 " + code + " " + reason(code) + "\r\n"
                + "Content-Type: " + type + "\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Cache-Control: no-cache\r\n"
                + "Connection: close\r\n\r\n";
        out.write(h.getBytes(UTF8));
        if (!head) out.write(body);
    }

    private static String reason(int code) {
        switch (code) {
            case 200: return "OK";
            case 400: return "Bad Request";
            case 403: return "Forbidden";
            case 404: return "Not Found";
            case 405: return "Method Not Allowed";
            case 413: return "Payload Too Large";
            case 429: return "Too Many Requests";
            case 503: return "Service Unavailable";
            default: return "Status";
        }
    }

    private static void closeQuietly(java.io.Closeable c) {
        if (c == null) return;
        try {
            c.close();
        } catch (IOException ignored) {
            // ignore
        }
    }

    /** Parsed request line and headers (header names lower-cased). */
    static final class Request {
        final String method;
        final String path;
        final Map<String, String> query;
        final Map<String, String> headers;
        /** Request body; empty when absent, null when larger than the limit. */
        final byte[] body;

        private Request(String method, String path, Map<String, String> query,
                        Map<String, String> headers, byte[] body) {
            this.method = method;
            this.path = path;
            this.query = query;
            this.headers = headers;
            this.body = body;
        }

        static Request read(InputStream in) throws IOException {
            String line = readLine(in);
            if (line == null || line.isEmpty()) return null;
            String[] parts = line.split(" ");
            if (parts.length < 2) return null;
            Map<String, String> headers = new HashMap<>();
            for (int i = 0; i < 64; i++) {
                String h = readLine(in);
                if (h == null || h.isEmpty()) break;
                int c = h.indexOf(':');
                if (c > 0) headers.put(h.substring(0, c).trim().toLowerCase(Locale.US), h.substring(c + 1).trim());
            }
            String target = parts[1];
            Map<String, String> query = new HashMap<>();
            int q = target.indexOf('?');
            String rawPath = q >= 0 ? target.substring(0, q) : target;
            if (q >= 0) {
                for (String kv : target.substring(q + 1).split("&")) {
                    int e = kv.indexOf('=');
                    if (e > 0) query.put(decode(kv.substring(0, e)), decode(kv.substring(e + 1)));
                    else if (!kv.isEmpty()) query.put(decode(kv), "");
                }
            }
            return new Request(parts[0].toUpperCase(Locale.US), decode(rawPath), query, headers,
                    readBody(in, headers.get("content-length")));
        }

        private static byte[] readBody(InputStream in, String contentLength) throws IOException {
            if (contentLength == null) return new byte[0];
            long len;
            try {
                len = Long.parseLong(contentLength.trim());
            } catch (NumberFormatException e) {
                return new byte[0];
            }
            if (len <= 0) return new byte[0];
            if (len > MAX_BODY_BYTES) return null;
            byte[] body = new byte[(int) len];
            int off = 0;
            while (off < body.length) {
                int n = in.read(body, off, body.length - off);
                if (n < 0) throw new SocketException("truncated body");
                off += n;
            }
            return body;
        }

        static String decode(String s) {
            try {
                return URLDecoder.decode(s, "UTF-8");
            } catch (Exception e) {
                return s;
            }
        }

        /** Reads a CRLF/LF terminated ASCII line, max 4 KB. */
        private static String readLine(InputStream in) throws IOException {
            StringBuilder sb = new StringBuilder();
            while (true) {
                int c = in.read();
                if (c < 0) return sb.length() == 0 ? null : sb.toString();
                if (c == '\n') break;
                if (c != '\r') sb.append((char) c);
                if (sb.length() > 4096) throw new SocketException("header too long");
            }
            return sb.toString();
        }
    }

    /** Tiny Base64 decoder (java.util.Base64 needs API 26; android.util.Base64 is not unit-testable). */
    static final class Base64 {
        private Base64() {}

        static byte[] decode(String s) {
            String t = s.replace("=", "");
            int outLen = t.length() * 3 / 4;
            byte[] out = new byte[outLen];
            int buf = 0;
            int bits = 0;
            int o = 0;
            for (int i = 0; i < t.length(); i++) {
                int v = value(t.charAt(i));
                if (v < 0) return null;
                buf = (buf << 6) | v;
                bits += 6;
                if (bits >= 8) {
                    bits -= 8;
                    if (o < outLen) out[o++] = (byte) (buf >> bits);
                }
            }
            return out;
        }

        private static int value(char c) {
            if (c >= 'A' && c <= 'Z') return c - 'A';
            if (c >= 'a' && c <= 'z') return c - 'a' + 26;
            if (c >= '0' && c <= '9') return c - '0' + 52;
            if (c == '+' || c == '-') return 62;
            if (c == '/' || c == '_') return 63;
            return -1;
        }
    }

    /** Reads a whole stream (used for the bundled web page). */
    static byte[] readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        return bos.toByteArray();
    }
}
