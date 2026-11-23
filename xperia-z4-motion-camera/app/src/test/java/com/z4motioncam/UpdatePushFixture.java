package com.z4motioncam;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashSet;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * The signed fake APKs in app/src/test/fixtures (throw-away test keys, rebuilt by make-fixtures.sh) and a
 * fake device that reads them like the package manager would: signers from the real JAR signature,
 * version code = 10 + the build number written in assets/marker.txt (signed-a = 11 is "installed").
 */
final class UpdatePushFixture {
    private UpdatePushFixture() {}

    static File fx(String name) {
        return new File("app/src/test/fixtures/" + name);
    }

    static byte[] bytes(File f) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        InputStream in = new FileInputStream(f);
        try {
            byte[] b = new byte[8192];
            int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
        } finally {
            in.close();
        }
        return out.toByteArray();
    }

    /** Rewrites an archive: replaces one entry's content and/or adds an entry (the signature stays as it was). */
    static void rewrite(String fixture, File to, String replaceEntry, byte[] replacement, String addEntry) throws IOException {
        ZipInputStream in = new ZipInputStream(new FileInputStream(fx(fixture)));
        ZipOutputStream out = new ZipOutputStream(new java.io.FileOutputStream(to));
        ZipEntry e;
        byte[] buf = new byte[8192];
        while ((e = in.getNextEntry()) != null) {
            ByteArrayOutputStream data = new ByteArrayOutputStream();
            int n;
            while ((n = in.read(buf)) > 0) data.write(buf, 0, n);
            out.putNextEntry(new ZipEntry(e.getName()));
            out.write(e.getName().equals(replaceEntry) ? replacement : data.toByteArray());
            out.closeEntry();
        }
        if (addEntry != null) {
            out.putNextEntry(new ZipEntry(addEntry));
            byte[] extra = new byte[2000];
            new java.util.Random(2).nextBytes(extra);
            out.write(extra);
            out.closeEntry();
        }
        out.close();
        in.close();
    }

    static final class Env implements UpdateEngine.Env {
        int installed = 11;
        String pkg = "com.z4motioncam";
        int versionOffset = 0;

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
            try {
                return ApkCheck.jarSigners(fx("signed-a.apk"));
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        }

        @Override
        public UpdateEngine.ApkInfo readApk(File apk) {
            try {
                ZipFile z = new ZipFile(apk);
                try {
                    ZipEntry m = z.getEntry("assets/marker.txt");
                    if (m == null) return null;
                    byte[] b = new byte[64];
                    int n = z.getInputStream(m).read(b);
                    int build = Integer.parseInt(new String(b, 0, n, "UTF-8").replaceAll("[^0-9]", ""));
                    Set<String> signers;
                    try {
                        signers = ApkCheck.jarSigners(apk);
                    } catch (SecurityException e) {
                        signers = new HashSet<>();
                    }
                    return new UpdateEngine.ApkInfo(pkg, 10 + build + versionOffset, signers, "1." + (10 + build + versionOffset));
                } finally {
                    z.close();
                }
            } catch (IOException | RuntimeException e) {
                return null;
            }
        }
    }
}
