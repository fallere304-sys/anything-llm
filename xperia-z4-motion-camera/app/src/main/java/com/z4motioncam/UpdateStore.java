package com.z4motioncam;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The folder where updates are downloaded: {@code update-N.apk.part} while downloading,
 * {@code update-N.apk} once verified, and {@code update-N.meta} (url, sha256, size) so a download
 * can be resumed only when it is the same file. Anything else in the folder, and anything stale,
 * is removed by {@link #cleanup}.
 */
class UpdateStore {
    static final long PART_MAX_AGE_MS = 3L * 24 * 3600_000L;
    private static final Pattern NAME = Pattern.compile("update-(\\d+)\\.(apk\\.part|apk|meta)");
    private static final Charset UTF8 = Charset.forName("UTF-8");

    static final class Meta {
        final String url;
        final String sha256;
        final long size;

        Meta(String url, String sha256, long size) {
            this.url = url;
            this.sha256 = sha256;
            this.size = size;
        }

        boolean matches(UpdateManifest m) {
            return url.equals(m.apkUrl) && sha256.equals(m.sha256) && size == m.size;
        }
    }

    private final File dir;

    UpdateStore(File dir) {
        this.dir = dir;
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
    }

    File dir() {
        return dir;
    }

    File part(int vc) {
        return new File(dir, "update-" + vc + ".apk.part");
    }

    File ready(int vc) {
        return new File(dir, "update-" + vc + ".apk");
    }

    private File meta(int vc) {
        return new File(dir, "update-" + vc + ".meta");
    }

    long usableBytes() {
        return dir.getUsableSpace();
    }

    void writeMeta(int vc, UpdateManifest m) throws IOException {
        File tmp = new File(dir, "update-" + vc + ".meta.tmp");
        OutputStream out = new FileOutputStream(tmp);
        try {
            out.write((m.apkUrl + "\n" + m.sha256 + "\n" + m.size + "\n").getBytes(UTF8));
        } finally {
            out.close();
        }
        if (!tmp.renameTo(meta(vc))) {
            //noinspection ResultOfMethodCallIgnored
            tmp.delete();
            throw new IOException("meta rename failed");
        }
    }

    Meta readMeta(int vc) {
        File f = meta(vc);
        if (!f.isFile()) return null;
        try {
            BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), UTF8));
            try {
                String url = r.readLine();
                String sha = r.readLine();
                String size = r.readLine();
                if (url == null || sha == null || size == null) return null;
                return new Meta(url, sha, Long.parseLong(size.trim()));
            } finally {
                r.close();
            }
        } catch (IOException | NumberFormatException e) {
            return null;
        }
    }

    void deleteVersion(int vc) {
        //noinspection ResultOfMethodCallIgnored
        part(vc).delete();
        //noinspection ResultOfMethodCallIgnored
        ready(vc).delete();
        //noinspection ResultOfMethodCallIgnored
        meta(vc).delete();
    }

    /** Highest verified-looking update newer than the installed one, or -1. */
    int newestReady(int installedVc) {
        int best = -1;
        File[] files = dir.listFiles();
        if (files == null) return -1;
        for (File f : files) {
            Matcher m = NAME.matcher(f.getName());
            if (!m.matches() || !m.group(2).equals("apk")) continue;
            int vc = Integer.parseInt(m.group(1).length() > 9 ? "0" : m.group(1));
            if (vc > installedVc && vc > best && readMeta(vc) != null) best = vc;
        }
        return best;
    }

    /**
     * Removes what is no longer useful: files that are not ours, versions that are installed or
     * older, versions other than {@code keepVc} (the newest one known; pass -1 when unknown to keep
     * every newer version), and part files older than three days (or dated in the future after a
     * clock change). Safe to call at any time except while a download is writing.
     */
    void cleanup(int installedVc, int keepVc, long nowMs) {
        File[] files = dir.listFiles();
        if (files == null) return;
        for (File f : files) {
            Matcher m = NAME.matcher(f.getName());
            boolean drop;
            if (!m.matches() || f.isDirectory()) {
                drop = true;
            } else {
                int vc = m.group(1).length() > 9 ? -1 : Integer.parseInt(m.group(1));
                drop = vc <= installedVc || (keepVc >= 0 && vc != keepVc);
                if (!drop && m.group(2).equals("apk.part")) {
                    long age = nowMs - f.lastModified();
                    drop = age > PART_MAX_AGE_MS || age < -24 * 3600_000L;
                }
            }
            if (drop) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
            }
        }
    }
}
