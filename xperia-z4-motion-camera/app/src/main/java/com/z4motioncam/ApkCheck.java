package com.z4motioncam;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Enumeration;
import java.util.Locale;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Static checks of a downloaded file before anyone is asked to install it (no Android needed). */
final class ApkCheck {
    private ApkCheck() {}

    static String sha256Hex(File f) throws IOException {
        MessageDigest md = digest();
        InputStream in = new FileInputStream(f);
        try {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
        } finally {
            in.close();
        }
        return hex(md.digest());
    }

    static String sha256Hex(byte[] data) {
        return hex(digest().digest(data));
    }

    private static MessageDigest digest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    static String hex(byte[] b) {
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format(Locale.US, "%02x", x & 0xff));
        return sb.toString();
    }

    /**
     * Size and structure: within the allowed size, a ZIP that can really be opened, holding an
     * Android manifest and code. This only filters out wrong or broken files (an HTML error page, a
     * cut-off download); who built it is decided by the signature check.
     */
    static void checkFormat(File f, long expectedSize) throws UpdateException {
        long len = f.length();
        if (len < UpdateManifest.MIN_APK_BYTES || len > UpdateManifest.MAX_APK_BYTES) {
            throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "APK の大きさが範囲外です（" + len + " バイト）");
        }
        if (expectedSize > 0 && len != expectedSize) {
            throw new UpdateException(UpdateException.Kind.BAD_FORMAT,
                    "APK の大きさが更新情報と違います（" + len + " バイト、期待 " + expectedSize + "）");
        }
        try {
            InputStream in = new FileInputStream(f);
            try {
                byte[] magic = new byte[4];
                if (in.read(magic) != 4 || magic[0] != 'P' || magic[1] != 'K' || magic[2] != 3 || magic[3] != 4) {
                    throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "APK ではありません（ZIP の形式ではありません）");
                }
            } finally {
                in.close();
            }
            boolean manifest = false;
            boolean dex = false;
            ZipFile z = new ZipFile(f);
            try {
                Enumeration<? extends ZipEntry> e = z.entries();
                int count = 0;
                while (e.hasMoreElements()) {
                    ZipEntry en = e.nextElement();
                    if (++count > 20_000) throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "APK の中身が多すぎます");
                    manifest |= en.getName().equals("AndroidManifest.xml");
                    dex |= en.getName().equals("classes.dex");
                }
            } finally {
                z.close();
            }
            if (!manifest || !dex) {
                throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "APK の中身が足りません（AndroidManifest.xml / classes.dex）");
            }
        } catch (UpdateException e) {
            throw e;
        } catch (IOException e) {
            throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "APK を開けません（壊れています）", e);
        }
    }

    /** Same, non-empty set of signing certificates (as lowercase SHA-256 hex) in both. */
    static boolean sameSigners(Set<String> installed, Set<String> candidate) {
        return installed != null && candidate != null && !installed.isEmpty() && installed.equals(candidate);
    }
}
