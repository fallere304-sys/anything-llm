package com.z4motioncam;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.MalformedURLException;
import java.net.URL;
import java.util.Locale;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLSocketFactory;

/**
 * HTTPS fetches for the update check: a small JSON, and a resumable file download. Only https://
 * is accepted, including every redirect hop (a redirect to http:// is refused); certificates and
 * host names are verified by the platform. Redirects are followed by hand so each hop is checked.
 */
final class UpdateHttp {
    interface Progress {
        void onBytes(long done, long total);

        boolean cancelled();
    }

    static final int MAX_REDIRECTS = 5;
    private static final int TIMEOUT_MS = 20_000;

    private final SSLSocketFactory tls;
    /** Unit tests talk to a local plain-HTTP server; the app never sets this. */
    private final boolean allowHttp;

    UpdateHttp(SSLSocketFactory tls, boolean allowHttp) {
        this.tls = tls;
        this.allowHttp = allowHttp;
    }

    static boolean isHttpsUrl(String s) {
        if (s == null) return false;
        String t = s.trim().toLowerCase(Locale.US);
        if (!t.startsWith("https://") || t.length() <= "https://".length()) return false;
        try {
            URL u = new URL(s.trim());
            return u.getHost() != null && !u.getHost().isEmpty();
        } catch (MalformedURLException e) {
            return false;
        }
    }

    private void requireHttps(String url) throws UpdateException {
        if (isHttpsUrl(url)) return;
        if (allowHttp && url != null && url.toLowerCase(Locale.US).startsWith("http://")) return;
        throw new UpdateException(UpdateException.Kind.NOT_HTTPS,
                "更新の取得先は https:// で始まるアドレスだけ使えます（暗号化されない通信では取得しません）");
    }

    private HttpURLConnection open(String url, String range) throws IOException {
        String current = url;
        for (int hop = 0; hop <= MAX_REDIRECTS; hop++) {
            requireHttps(current);
            HttpURLConnection c = (HttpURLConnection) new URL(current).openConnection();
            if (tls != null && c instanceof HttpsURLConnection) ((HttpsURLConnection) c).setSSLSocketFactory(tls);
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(TIMEOUT_MS);
            c.setReadTimeout(TIMEOUT_MS);
            c.setUseCaches(false);
            c.setRequestProperty("User-Agent", "Z4MotionCam-updater");
            c.setRequestProperty("Accept-Encoding", "identity"); // byte ranges and the hash need the raw file
            if (range != null) c.setRequestProperty("Range", range);
            int code = c.getResponseCode();
            if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) {
                String loc = c.getHeaderField("Location");
                c.disconnect();
                if (loc == null) throw new UpdateException(UpdateException.Kind.NETWORK, "転送先が示されていません");
                current = new URL(new URL(current), loc).toString();
                continue;
            }
            return c;
        }
        throw new UpdateException(UpdateException.Kind.NETWORK, "転送が多すぎます");
    }

    /** GET a small text/JSON document (at most {@code limit} bytes). */
    byte[] getBytes(String url, int limit) throws UpdateException {
        HttpURLConnection c = null;
        try {
            c = open(url, null);
            int code = c.getResponseCode();
            if (code / 100 != 2) throw new UpdateException(UpdateException.Kind.NETWORK, "サーバーが HTTP " + code + " を返しました");
            InputStream in = c.getInputStream();
            try {
                java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    if (out.size() > limit) throw new UpdateException(UpdateException.Kind.BAD_MANIFEST, "更新情報が大きすぎます");
                }
                return out.toByteArray();
            } finally {
                in.close();
            }
        } catch (UpdateException e) {
            throw e;
        } catch (IOException e) {
            throw new UpdateException(UpdateException.Kind.NETWORK, "通信できませんでした（" + e.getClass().getSimpleName()
                    + (e.getMessage() == null ? "" : ": " + e.getMessage()) + "）", e);
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Downloads {@code url} into {@code part}, continuing from its current length when the server
     * supports ranges (and starting over when it does not). The file never grows past
     * {@code expectedSize}. On a network error the partial file stays for the next attempt.
     *
     * @return the file length after the download (equals expectedSize when complete)
     */
    long download(String url, File part, long expectedSize, Progress progress) throws UpdateException {
        long have = part.isFile() ? part.length() : 0;
        if (have > expectedSize) {
            //noinspection ResultOfMethodCallIgnored
            part.delete();
            have = 0;
        }
        if (have == expectedSize) return have;
        HttpURLConnection c = null;
        try {
            for (int attempt = 0; attempt < 2; attempt++) {
                c = open(url, have > 0 ? "bytes=" + have + "-" : null);
                int code = c.getResponseCode();
                if (code == 416) { // our offset is not valid for this file: start over
                    c.disconnect();
                    //noinspection ResultOfMethodCallIgnored
                    part.delete();
                    have = 0;
                    continue;
                }
                if (code / 100 != 2) {
                    throw new UpdateException(UpdateException.Kind.NETWORK, "サーバーが HTTP " + code + " を返しました");
                }
                boolean resumed = code == 206;
                if (resumed && !rangeStartsAt(c.getHeaderField("Content-Range"), have)) {
                    c.disconnect();
                    //noinspection ResultOfMethodCallIgnored
                    part.delete();
                    have = 0;
                    continue;
                }
                if (!resumed && have > 0) { // the server ignored Range and sends everything
                    have = 0;
                }
                long remaining = c.getContentLength() >= 0 ? c.getContentLength() : -1;
                if (remaining >= 0 && have + remaining != expectedSize) {
                    throw new UpdateException(UpdateException.Kind.TOO_LARGE,
                            "ファイルの大きさが更新情報と一致しません（" + (have + remaining) + " バイト、期待 " + expectedSize + "）");
                }
                return copy(c, part, have, expectedSize, progress);
            }
            throw new UpdateException(UpdateException.Kind.NETWORK, "途中からの再開に失敗しました");
        } catch (UpdateException e) {
            throw e;
        } catch (IOException e) {
            throw new UpdateException(UpdateException.Kind.NETWORK, "ダウンロードが途中で止まりました（"
                    + e.getClass().getSimpleName() + "）。あとで続きから再開できます", e);
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static long copy(HttpURLConnection c, File part, long have, long expected, Progress progress)
            throws IOException {
        if (have == 0) {
            OutputStream truncate = new FileOutputStream(part, false);
            truncate.close();
        }
        InputStream in = c.getInputStream();
        RandomAccessFile out = new RandomAccessFile(part, "rw");
        try {
            out.seek(have);
            byte[] buf = new byte[32 * 1024];
            long total = have;
            int n;
            while ((n = in.read(buf)) > 0) {
                if (progress != null && progress.cancelled()) {
                    throw new UpdateException(UpdateException.Kind.CANCELLED, "ダウンロードを中止しました");
                }
                total += n;
                if (total > expected) {
                    throw new UpdateException(UpdateException.Kind.TOO_LARGE, "更新情報より大きなファイルが送られてきました");
                }
                out.write(buf, 0, n);
                if (progress != null) progress.onBytes(total, expected);
            }
            out.getFD().sync();
            if (total < expected) throw new IOException("connection closed early"); // the part stays for a resume
            return total;
        } finally {
            out.close();
            in.close();
        }
    }

    /** "bytes 100-999/1000" -> starts at 100? */
    static boolean rangeStartsAt(String contentRange, long start) {
        if (contentRange == null) return false;
        String t = contentRange.trim().toLowerCase(Locale.US);
        if (!t.startsWith("bytes ")) return false;
        int dash = t.indexOf('-');
        if (dash < 0) return false;
        try {
            return Long.parseLong(t.substring(6, dash).trim()) == start;
        } catch (NumberFormatException e) {
            return false;
        }
    }
}
