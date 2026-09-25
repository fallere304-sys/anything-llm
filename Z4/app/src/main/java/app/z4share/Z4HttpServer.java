package app.z4share;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.net.URLConnection;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.Charset;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

/**
 * iPhone の Safari から使う小さな HTTP サーバー。Android API に依存しないので JVM 上でテストできる。
 *
 * <pre>
 * GET  /?k=TOKEN            送受信ページ (index.html)
 * GET  /api/files           Z4 → iPhone の送信リストと受信済みリスト (JSON)
 * GET  /dl/NAME             送信リストのファイルをダウンロード (Range 対応, ?inline=1 で表示)
 * POST /api/upload?name=N   リクエストボディをそのまま受信フォルダに保存
 * </pre>
 *
 * すべてのリクエストは QR コードに埋め込んだトークン (?k= か Cookie) を要求する。
 */
public final class Z4HttpServer {

    public interface Listener {
        void onFileReceived(File file);

        void onError(String message);
    }

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final Charset LATIN1 = Charset.forName("ISO-8859-1");
    private static final String COOKIE = "z4k";
    private static final int MAX_HEADER_BYTES = 16 * 1024;
    private static final int MAX_HEADER_LINES = 100;
    private static final int SOCKET_TIMEOUT_MS = 60_000;
    private static final long RESERVED_FREE_BYTES = 20L * 1024 * 1024;
    private static final int MAX_NAME_CHARS = 120;
    static final String PART_SUFFIX = ".z4part";

    private final File outboxDir;
    private volatile File inboxDir;
    private final String token;
    private final byte[] indexHtml;
    private final Listener listener;
    private final String deviceName;

    private final ExecutorService workers = Executors.newFixedThreadPool(8);
    private final Object inboxLock = new Object();
    private volatile ServerSocket serverSocket;
    private Thread acceptThread;

    public Z4HttpServer(File outboxDir, File inboxDir, String token, byte[] indexHtml,
                        String deviceName, Listener listener) {
        this.outboxDir = outboxDir;
        this.inboxDir = inboxDir;
        this.token = token;
        this.indexHtml = indexHtml;
        this.deviceName = deviceName;
        this.listener = listener;
    }

    /** preferredPort から順に空いているポートを探して待ち受けを始め、実際のポート番号を返す。 */
    public synchronized int start(int preferredPort) throws IOException {
        if (serverSocket != null) {
            return serverSocket.getLocalPort();
        }
        IOException last = null;
        for (int port = preferredPort; port < preferredPort + 20; port++) {
            ServerSocket s = new ServerSocket();
            try {
                s.setReuseAddress(true);
                s.bind(new InetSocketAddress(port));
                serverSocket = s;
                break;
            } catch (IOException e) {
                last = e;
                s.close();
            }
        }
        if (serverSocket == null) {
            throw last != null ? last : new IOException("no free port");
        }
        final ServerSocket ss = serverSocket;
        acceptThread = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop(ss);
            }
        }, "z4-accept");
        acceptThread.start();
        return ss.getLocalPort();
    }

    public synchronized void stop() {
        ServerSocket s = serverSocket;
        serverSocket = null;
        if (s != null) {
            try {
                s.close();
            } catch (IOException ignored) {
            }
        }
        workers.shutdownNow();
    }

    public void setInboxDir(File dir) {
        inboxDir = dir;
    }

    public File getInboxDir() {
        return inboxDir;
    }

    private void acceptLoop(ServerSocket ss) {
        while (!ss.isClosed()) {
            final Socket socket;
            try {
                socket = ss.accept();
            } catch (IOException e) {
                if (!ss.isClosed() && listener != null) {
                    listener.onError("accept: " + e.getMessage());
                }
                continue;
            }
            try {
                workers.execute(new Runnable() {
                    @Override
                    public void run() {
                        handle(socket);
                    }
                });
            } catch (RejectedExecutionException e) {
                closeQuietly(socket);
            }
        }
    }

    // ---------------------------------------------------------------- request handling

    private static final class Request {
        String method;
        String path;
        Map<String, String> query = new HashMap<>();
        Map<String, String> headers = new HashMap<>();
        InputStream body;
    }

    private void handle(Socket socket) {
        try {
            socket.setSoTimeout(SOCKET_TIMEOUT_MS);
            InputStream in = new BufferedInputStream(socket.getInputStream(), 64 * 1024);
            OutputStream out = socket.getOutputStream();
            Request req;
            try {
                req = readRequest(in);
            } catch (HttpError e) {
                sendText(out, e.status, e.getMessage(), null);
                return;
            }
            if (req == null) {
                return;
            }
            try {
                route(req, out);
            } catch (HttpError e) {
                sendText(out, e.status, e.getMessage(), null);
            }
            out.flush();
        } catch (SocketException ignored) {
            // iPhone 側がキャンセルした等
        } catch (IOException e) {
            if (listener != null) {
                listener.onError(e.getMessage());
            }
        } finally {
            closeQuietly(socket);
        }
    }

    private void route(Request req, OutputStream out) throws IOException, HttpError {
        boolean head = "HEAD".equals(req.method);
        boolean get = head || "GET".equals(req.method);
        String setCookie = null;

        String queryToken = req.query.get("k");
        if (queryToken != null && tokenMatches(queryToken)) {
            setCookie = COOKIE + "=" + token + "; Path=/; HttpOnly; SameSite=Lax";
        } else if (!tokenMatches(cookieValue(req.headers.get("cookie"), COOKIE))) {
            sendText(out, 403, "Forbidden: Z4 の画面に表示された QR コードから開いてください。", null);
            return;
        }

        if (get && ("/".equals(req.path) || "/index.html".equals(req.path))) {
            Map<String, String> h = new HashMap<>();
            h.put("Cache-Control", "no-store");
            if (setCookie != null) {
                h.put("Set-Cookie", setCookie);
            }
            sendBytes(out, 200, "text/html; charset=utf-8", indexHtml, h, head);
        } else if (get && "/api/files".equals(req.path)) {
            Map<String, String> h = new HashMap<>();
            h.put("Cache-Control", "no-store");
            sendBytes(out, 200, "application/json; charset=utf-8", filesJson().getBytes(UTF8), h, head);
        } else if (get && req.path.startsWith("/dl/")) {
            serveDownload(req, out, head);
        } else if ("POST".equals(req.method) && "/api/upload".equals(req.path)) {
            receiveUpload(req, out);
        } else if ("OPTIONS".equals(req.method)) {
            sendText(out, 204, "", null);
        } else {
            sendText(out, 404, "Not Found", null);
        }
    }

    // ---------------------------------------------------------------- Z4 -> iPhone

    private void serveDownload(Request req, OutputStream out, boolean head) throws IOException, HttpError {
        String name = req.path.substring("/dl/".length());
        File file = resolveChild(outboxDir, name);
        if (file == null || !file.isFile()) {
            throw new HttpError(404, "ファイルが見つかりません");
        }
        long length = file.length();
        long start = 0;
        long end = length - 1;
        int status = 200;
        Map<String, String> h = new HashMap<>();
        h.put("Accept-Ranges", "bytes");
        h.put("Cache-Control", "no-store");
        boolean inline = "1".equals(req.query.get("inline"));
        h.put("Content-Disposition", contentDisposition(inline ? "inline" : "attachment", file.getName()));

        String range = req.headers.get("range");
        if (range != null && length > 0) {
            long[] r = parseRange(range, length);
            if (r == null) {
                h.put("Content-Range", "bytes */" + length);
                writeHead(out, 416, "text/plain; charset=utf-8", 0, h);
                return;
            }
            start = r[0];
            end = r[1];
            status = 206;
            h.put("Content-Range", "bytes " + start + "-" + end + "/" + length);
        }
        long count = length == 0 ? 0 : end - start + 1;
        writeHead(out, status, guessMime(file.getName()), count, h);
        if (head || count == 0) {
            return;
        }
        try (FileInputStream fin = new FileInputStream(file)) {
            skipFully(fin, start);
            copy(fin, out, count);
        }
    }

    /** "bytes=a-b" / "bytes=a-" / "bytes=-n" の単一範囲だけ扱う。満たせない範囲は null。 */
    static long[] parseRange(String header, long length) {
        String h = header.trim();
        if (!h.startsWith("bytes=") || h.indexOf(',') >= 0) {
            return null;
        }
        String spec = h.substring(6).trim();
        int dash = spec.indexOf('-');
        if (dash < 0) {
            return null;
        }
        try {
            String a = spec.substring(0, dash).trim();
            String b = spec.substring(dash + 1).trim();
            long start;
            long end;
            if (a.isEmpty()) {
                long suffix = Long.parseLong(b);
                if (suffix <= 0) {
                    return null;
                }
                start = Math.max(0, length - suffix);
                end = length - 1;
            } else {
                start = Long.parseLong(a);
                end = b.isEmpty() ? length - 1 : Math.min(Long.parseLong(b), length - 1);
            }
            if (start < 0 || start >= length || end < start) {
                return null;
            }
            return new long[]{start, end};
        } catch (NumberFormatException e) {
            return null;
        }
    }

    // ---------------------------------------------------------------- iPhone -> Z4

    private void receiveUpload(Request req, OutputStream out) throws IOException, HttpError {
        String rawName = req.query.get("name");
        String name = sanitizeFileName(rawName);
        String cl = req.headers.get("content-length");
        if (cl == null) {
            throw new HttpError(411, "Content-Length が必要です");
        }
        long length;
        try {
            length = Long.parseLong(cl.trim());
        } catch (NumberFormatException e) {
            throw new HttpError(400, "Content-Length が不正です");
        }
        if (length < 0) {
            throw new HttpError(400, "Content-Length が不正です");
        }
        File dir = inboxDir;
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new HttpError(500, "受信フォルダを作れません: " + dir);
        }
        if (dir.getUsableSpace() < length + RESERVED_FREE_BYTES) {
            throw new HttpError(507, "Z4 の空き容量が足りません");
        }
        if ("100-continue".equalsIgnoreCase(req.headers.get("expect"))) {
            out.write("HTTP/1.1 100 Continue\r\n\r\n".getBytes(LATIN1));
            out.flush();
        }

        File part = File.createTempFile("upload-", PART_SUFFIX, dir);
        boolean ok = false;
        try {
            try (FileOutputStream fout = new FileOutputStream(part)) {
                long got = copy(req.body, fout, length);
                if (got != length) {
                    throw new HttpError(400, "受信が途中で切れました");
                }
                fout.getFD().sync();
            }
            File dest;
            synchronized (inboxLock) {
                dest = uniqueFile(dir, name);
                if (!part.renameTo(dest)) {
                    throw new HttpError(500, "保存に失敗しました");
                }
            }
            ok = true;
            if (listener != null) {
                listener.onFileReceived(dest);
            }
            Map<String, String> h = new HashMap<>();
            h.put("Cache-Control", "no-store");
            String json = "{\"saved\":" + jsonString(dest.getName()) + ",\"size\":" + dest.length() + "}";
            sendBytes(out, 200, "application/json; charset=utf-8", json.getBytes(UTF8), h, false);
        } finally {
            if (!ok) {
                //noinspection ResultOfMethodCallIgnored
                part.delete();
            }
        }
    }

    static String sanitizeFileName(String raw) {
        String n = raw == null ? "" : raw;
        int slash = Math.max(n.lastIndexOf('/'), n.lastIndexOf('\\'));
        if (slash >= 0) {
            n = n.substring(slash + 1);
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < n.length(); i++) {
            char c = n.charAt(i);
            if (c < 0x20 || c == 0x7f || ":*?\"<>|".indexOf(c) >= 0) {
                sb.append('_');
            } else {
                sb.append(c);
            }
        }
        n = sb.toString().trim();
        while (n.startsWith(".")) {
            n = n.substring(1);
        }
        if (n.endsWith(PART_SUFFIX)) {
            n = n + "_";
        }
        if (n.isEmpty()) {
            n = "file";
        }
        if (n.length() > MAX_NAME_CHARS) {
            int dot = n.lastIndexOf('.');
            String ext = dot > 0 && n.length() - dot <= 10 ? n.substring(dot) : "";
            n = n.substring(0, MAX_NAME_CHARS - ext.length()) + ext;
        }
        return n;
    }

    static File uniqueFile(File dir, String name) {
        File f = new File(dir, name);
        if (!f.exists()) {
            return f;
        }
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        String ext = dot > 0 ? name.substring(dot) : "";
        for (int i = 1; ; i++) {
            f = new File(dir, base + " (" + i + ")" + ext);
            if (!f.exists()) {
                return f;
            }
        }
    }

    // ---------------------------------------------------------------- listing

    String filesJson() {
        StringBuilder sb = new StringBuilder();
        sb.append("{\"device\":").append(jsonString(deviceName));
        sb.append(",\"outbox\":[");
        appendFiles(sb, listFiles(outboxDir), true);
        sb.append("],\"received\":[");
        appendFiles(sb, listFiles(inboxDir), false);
        sb.append("]}");
        return sb.toString();
    }

    private void appendFiles(StringBuilder sb, List<File> files, boolean withUrl) {
        boolean first = true;
        for (File f : files) {
            if (!first) {
                sb.append(',');
            }
            first = false;
            sb.append("{\"name\":").append(jsonString(f.getName()));
            sb.append(",\"size\":").append(f.length());
            sb.append(",\"modified\":").append(f.lastModified());
            if (withUrl) {
                sb.append(",\"url\":").append(jsonString("/dl/" + urlEncodePath(f.getName())));
            }
            sb.append('}');
        }
    }

    /** 表示対象のファイル一覧 (新しい順、書き込み中の一時ファイルは除く)。 */
    public static List<File> listFiles(File dir) {
        File[] files = dir == null ? null : dir.listFiles();
        if (files == null) {
            return Collections.emptyList();
        }
        List<File> result = new ArrayList<>();
        for (File f : files) {
            if (f.isFile() && !f.getName().endsWith(PART_SUFFIX) && !f.getName().startsWith(".")) {
                result.add(f);
            }
        }
        Collections.sort(result, new Comparator<File>() {
            @Override
            public int compare(File a, File b) {
                long d = b.lastModified() - a.lastModified();
                return d > 0 ? 1 : d < 0 ? -1 : a.getName().compareTo(b.getName());
            }
        });
        return result;
    }

    /** dir 直下の name を返す。パストラバーサルなら null。 */
    static File resolveChild(File dir, String name) {
        if (name.isEmpty() || name.indexOf('/') >= 0 || name.indexOf('\\') >= 0
                || ".".equals(name) || "..".equals(name) || name.indexOf('\0') >= 0) {
            return null;
        }
        try {
            File f = new File(dir, name);
            if (!f.getCanonicalFile().getParentFile().equals(dir.getCanonicalFile())) {
                return null;
            }
            return f;
        } catch (IOException e) {
            return null;
        }
    }

    // ---------------------------------------------------------------- HTTP parsing

    private static final class HttpError extends Exception {
        private static final long serialVersionUID = 1L;
        final int status;

        HttpError(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    private Request readRequest(InputStream in) throws IOException, HttpError {
        int[] budget = {MAX_HEADER_BYTES};
        String requestLine = readLine(in, budget);
        if (requestLine == null) {
            return null;
        }
        String[] parts = requestLine.split(" ");
        if (parts.length != 3 || !parts[2].startsWith("HTTP/")) {
            throw new HttpError(400, "Bad Request");
        }
        Request req = new Request();
        req.method = parts[0].toUpperCase(Locale.US);
        String target = parts[1];
        int q = target.indexOf('?');
        String rawPath = q >= 0 ? target.substring(0, q) : target;
        req.path = urlDecode(rawPath, false);
        if (q >= 0) {
            parseQuery(target.substring(q + 1), req.query);
        }
        for (int i = 0; ; i++) {
            if (i > MAX_HEADER_LINES) {
                throw new HttpError(431, "Too many headers");
            }
            String line = readLine(in, budget);
            if (line == null) {
                throw new HttpError(400, "Bad Request");
            }
            if (line.isEmpty()) {
                break;
            }
            int colon = line.indexOf(':');
            if (colon > 0) {
                String key = line.substring(0, colon).trim().toLowerCase(Locale.US);
                String value = line.substring(colon + 1).trim();
                String prev = req.headers.get(key);
                req.headers.put(key, prev == null ? value : prev + ("cookie".equals(key) ? "; " : ", ") + value);
            }
        }
        if (req.headers.containsKey("transfer-encoding")) {
            throw new HttpError(411, "chunked 転送には未対応です");
        }
        req.body = in;
        return req;
    }

    private static String readLine(InputStream in, int[] budget) throws IOException, HttpError {
        ByteArrayOutputStream buf = new ByteArrayOutputStream(128);
        int c;
        boolean any = false;
        while ((c = in.read()) != -1) {
            any = true;
            if (--budget[0] < 0) {
                throw new HttpError(431, "Header too large");
            }
            if (c == '\n') {
                byte[] b = buf.toByteArray();
                int len = b.length > 0 && b[b.length - 1] == '\r' ? b.length - 1 : b.length;
                return new String(b, 0, len, LATIN1);
            }
            buf.write(c);
        }
        return any ? new String(buf.toByteArray(), LATIN1) : null;
    }

    static void parseQuery(String query, Map<String, String> out) {
        for (String pair : query.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String k = eq >= 0 ? pair.substring(0, eq) : pair;
            String v = eq >= 0 ? pair.substring(eq + 1) : "";
            out.put(urlDecode(k, true), urlDecode(v, true));
        }
    }

    private static String urlDecode(String s, boolean plusIsSpace) {
        try {
            // URLDecoder は '+' を空白にするので、パスでは先に %2B へ置き換える
            return URLDecoder.decode(plusIsSpace ? s : s.replace("+", "%2B"), "UTF-8");
        } catch (IllegalArgumentException | UnsupportedEncodingException e) {
            return s;
        }
    }

    static String urlEncodePath(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8").replace("+", "%20");
        } catch (UnsupportedEncodingException e) {
            throw new AssertionError(e);
        }
    }

    static String cookieValue(String header, String name) {
        if (header == null) {
            return null;
        }
        for (String part : header.split(";")) {
            String p = part.trim();
            if (p.startsWith(name + "=")) {
                return p.substring(name.length() + 1);
            }
        }
        return null;
    }

    private boolean tokenMatches(String candidate) {
        return candidate != null
                && MessageDigest.isEqual(candidate.getBytes(UTF8), token.getBytes(UTF8));
    }

    // ---------------------------------------------------------------- HTTP writing

    private static String reason(int status) {
        switch (status) {
            case 200: return "OK";
            case 204: return "No Content";
            case 206: return "Partial Content";
            case 400: return "Bad Request";
            case 403: return "Forbidden";
            case 404: return "Not Found";
            case 411: return "Length Required";
            case 416: return "Range Not Satisfiable";
            case 431: return "Request Header Fields Too Large";
            case 507: return "Insufficient Storage";
            default: return "Error";
        }
    }

    private static void writeHead(OutputStream out, int status, String contentType, long length,
                                  Map<String, String> extra) throws IOException {
        StringBuilder sb = new StringBuilder();
        sb.append("HTTP/1.1 ").append(status).append(' ').append(reason(status)).append("\r\n");
        sb.append("Content-Type: ").append(contentType).append("\r\n");
        sb.append("Content-Length: ").append(length).append("\r\n");
        sb.append("Connection: close\r\n");
        sb.append("X-Content-Type-Options: nosniff\r\n");
        if (extra != null) {
            for (Map.Entry<String, String> e : extra.entrySet()) {
                sb.append(e.getKey()).append(": ").append(e.getValue()).append("\r\n");
            }
        }
        sb.append("\r\n");
        out.write(sb.toString().getBytes(UTF8));
    }

    private static void sendBytes(OutputStream out, int status, String contentType, byte[] body,
                                  Map<String, String> extra, boolean head) throws IOException {
        writeHead(out, status, contentType, body.length, extra);
        if (!head) {
            out.write(body);
        }
    }

    private static void sendText(OutputStream out, int status, String text, Map<String, String> extra)
            throws IOException {
        sendBytes(out, status, "text/plain; charset=utf-8", text.getBytes(UTF8), extra, false);
    }

    static String contentDisposition(String type, String fileName) {
        StringBuilder ascii = new StringBuilder();
        for (int i = 0; i < fileName.length(); i++) {
            char c = fileName.charAt(i);
            ascii.append(c >= 0x20 && c < 0x7f && c != '"' && c != '\\' ? c : '_');
        }
        return type + "; filename=\"" + ascii + "\"; filename*=UTF-8''" + urlEncodePath(fileName);
    }

    static String guessMime(String name) {
        String lower = name.toLowerCase(Locale.US);
        if (lower.endsWith(".heic")) {
            return "image/heic";
        }
        if (lower.endsWith(".mov")) {
            return "video/quicktime";
        }
        if (lower.endsWith(".mp4") || lower.endsWith(".m4v")) {
            return "video/mp4";
        }
        String m = URLConnection.guessContentTypeFromName(name);
        return m != null ? m : "application/octet-stream";
    }

    static String jsonString(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 2).append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20 || c == 0x2028 || c == 0x2029) {
                        sb.append(String.format(Locale.US, "\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.append('"').toString();
    }

    // ---------------------------------------------------------------- io helpers

    private static long copy(InputStream in, OutputStream out, long max) throws IOException {
        byte[] buf = new byte[64 * 1024];
        long total = 0;
        while (total < max) {
            int n = in.read(buf, 0, (int) Math.min(buf.length, max - total));
            if (n < 0) {
                break;
            }
            out.write(buf, 0, n);
            total += n;
        }
        return total;
    }

    private static void skipFully(InputStream in, long n) throws IOException {
        while (n > 0) {
            long s = in.skip(n);
            if (s <= 0) {
                throw new IOException("skip failed");
            }
            n -= s;
        }
    }

    private static void closeQuietly(Socket s) {
        try {
            s.close();
        } catch (IOException ignored) {
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) != -1) {
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }
}
