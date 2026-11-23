package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.HashSet;
import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/** The pull-style update against a local server: check, download, resume, verify, refuse. */
public class UpdateEngineTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private UpdateFixture srv;
    private UpdateFixture.FakeEnv env;
    private UpdateStore store;
    private UpdateEngine engine;
    private byte[] apk12;

    @Before
    public void setUp() throws Exception {
        srv = new UpdateFixture(false);
        env = new UpdateFixture.FakeEnv();
        store = new UpdateStore(tmp.newFolder("updates"));
        engine = new UpdateEngine(store, env, new UpdateHttp(null, true));
        apk12 = UpdateFixture.apk("com.z4motioncam", 12, "aa", 1);
        publish(12, apk12, null);
    }

    @After
    public void tearDown() {
        if (srv != null) srv.stop();
    }

    private void publish(int vc, byte[] apk, String shaOverride) {
        srv.route("/update.json").body = UpdateFixture.manifest("com.z4motioncam", vc, "1." + vc, "z4-" + vc + ".apk", apk, shaOverride);
        srv.route("/z4-" + vc + ".apk").body = apk;
    }

    private UpdateException expectFailure(UpdateException.Kind kind, UpdateManifest m) {
        try {
            engine.download(m, null);
        } catch (UpdateException e) {
            assertEquals(e.userMessage, kind, e.kind);
            return e;
        }
        fail("expected " + kind);
        return null;
    }

    private UpdateManifest manifest() throws Exception {
        UpdateManifest m = engine.check(srv.url("/update.json"), "");
        assertNotNull(m);
        return m;
    }

    // ---- check ----

    @Test
    public void findsANewerVersion() throws Exception {
        UpdateManifest m = manifest();
        assertEquals(12, m.versionCode);
        assertEquals("1.12", m.versionName);
        assertEquals(srv.url("/z4-12.apk"), m.apkUrl); // relative path resolved
    }

    @Test
    public void sameOrOlderVersionIsUpToDate() throws Exception {
        env.installed = 12;
        assertNull(engine.check(srv.url("/update.json"), ""));
        env.installed = 40;
        assertNull(engine.check(srv.url("/update.json"), ""));
    }

    @Test
    public void aManifestForAnotherPackageIsRefused() throws Exception {
        srv.route("/update.json").body = UpdateFixture.manifest("com.evil.app", 99, "9", "x.apk", apk12, null);
        try {
            engine.check(srv.url("/update.json"), "");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.BAD_MANIFEST, e.kind);
        }
    }

    @Test
    public void garbageManifestIsRefused() {
        srv.route("/update.json").body = "<html>404 not found</html>".getBytes();
        try {
            engine.check(srv.url("/update.json"), "");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.BAD_MANIFEST, e.kind);
        }
    }

    @Test
    public void serverErrorIsANetworkProblem() {
        srv.route("/update.json").status = 503;
        try {
            engine.check(srv.url("/update.json"), "");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.NETWORK, e.kind);
            assertTrue(e.retryable());
            assertTrue(e.userMessage.contains("503"));
        }
    }

    @Test
    public void unreachableServerIsANetworkProblem() {
        srv.stop();
        try {
            engine.check(srv.url("/update.json"), "");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.NETWORK, e.kind);
        }
        srv = null;
    }

    @Test
    public void aPinnedShaMustMatchTheManifest() throws Exception {
        String good = UpdateFixture.sha(apk12);
        assertNotNull(engine.check(srv.url("/update.json"), good));
        assertNotNull(engine.check(srv.url("/update.json"), good.toUpperCase()));
        try {
            engine.check(srv.url("/update.json"), "0000000000000000000000000000000000000000000000000000000000000000");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.PIN_MISMATCH, e.kind);
        }
    }

    @Test
    public void aTamperedManifestAndApkPairIsStillCaughtByThePin() throws Exception {
        // The attacker replaced both the manifest and the APK consistently: only the pin (or the signature) stops it.
        byte[] evil = UpdateFixture.apk("com.z4motioncam", 12, "aa", 99);
        publish(12, evil, null);
        try {
            engine.check(srv.url("/update.json"), UpdateFixture.sha(apk12));
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.PIN_MISMATCH, e.kind);
        }
    }

    // ---- download ----

    @Test
    public void downloadsAndVerifies() throws Exception {
        UpdateManifest m = manifest();
        File f = engine.download(m, null);
        assertEquals(store.ready(12), f);
        assertTrue(Arrays.equals(apk12, Files.readAllBytes(f.toPath())));
        assertFalse(store.part(12).exists());
        assertEquals(12, store.newestReady(11));
    }

    @Test
    public void reportsProgress() throws Exception {
        final long[] last = {0, 0};
        engine.download(manifest(), new UpdateHttp.Progress() {
            @Override public void onBytes(long done, long total) { last[0] = done; last[1] = total; }
            @Override public boolean cancelled() { return false; }
        });
        assertEquals(apk12.length, last[0]);
        assertEquals(apk12.length, last[1]);
    }

    @Test
    public void downloadingAgainReusesTheVerifiedFile() throws Exception {
        UpdateManifest m = manifest();
        engine.download(m, null);
        int hits = srv.routes.get("/z4-12.apk").hits;
        engine.download(m, null);
        assertEquals(hits, srv.routes.get("/z4-12.apk").hits);
    }

    @Test
    public void aWrongShaIsDiscarded() throws Exception {
        publish(12, apk12, "1111111111111111111111111111111111111111111111111111111111111111");
        UpdateManifest m = manifest();
        expectFailure(UpdateException.Kind.SHA_MISMATCH, m);
        assertFalse(store.part(12).exists());
        assertFalse(store.ready(12).exists());
        assertEquals(-1, store.newestReady(11));
    }

    @Test
    public void aSwappedApkIsDiscarded() throws Exception {
        UpdateManifest m = manifest();
        byte[] other = UpdateFixture.apk("com.z4motioncam", 12, "aa", 2); // same size, other content
        srv.route("/z4-12.apk").body = other;
        expectFailure(UpdateException.Kind.SHA_MISMATCH, m);
        assertFalse(store.ready(12).exists());
    }

    @Test
    public void anHtmlErrorPageIsNotAnApk() throws Exception {
        byte[] page = new byte[apk12.length];
        Arrays.fill(page, (byte) 'x');
        System.arraycopy("<html>".getBytes(), 0, page, 0, 6);
        publish(12, page, null);
        expectFailure(UpdateException.Kind.BAD_FORMAT, manifest());
        assertFalse(store.ready(12).exists());
    }

    @Test
    public void aZipWithoutCodeIsRefused() throws Exception {
        java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
        java.util.zip.ZipOutputStream z = new java.util.zip.ZipOutputStream(b);
        z.setLevel(0);
        z.putNextEntry(new java.util.zip.ZipEntry("readme.txt"));
        z.write(new byte[30_000]);
        z.close();
        publish(12, b.toByteArray(), null);
        expectFailure(UpdateException.Kind.BAD_FORMAT, manifest());
    }

    @Test
    public void aSizeThatDisagreesWithTheServerIsRefused() throws Exception {
        UpdateManifest m = manifest();
        srv.route("/z4-12.apk").body = Arrays.copyOf(apk12, apk12.length + 500);
        expectFailure(UpdateException.Kind.TOO_LARGE, m);
        assertFalse(store.part(12).exists());
    }

    @Test
    public void aTruncatedServerFileFailsAndKeepsNothingBad() throws Exception {
        UpdateManifest m = manifest();
        srv.route("/z4-12.apk").body = Arrays.copyOf(apk12, apk12.length - 1000);
        try {
            engine.download(m, null);
            fail();
        } catch (UpdateException e) {
            assertTrue(e.kind == UpdateException.Kind.TOO_LARGE || e.kind == UpdateException.Kind.NETWORK);
        }
        assertFalse(store.ready(12).exists());
    }

    // ---- interruption and resume ----

    @Test
    public void resumesAfterTheConnectionDropped() throws Exception {
        UpdateManifest m = manifest();
        UpdateFixture.Route r = srv.routes.get("/z4-12.apk");
        r.cutAfter = 30_000;
        try {
            engine.download(m, null);
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.NETWORK, e.kind);
            assertTrue(e.userMessage.contains("再開"));
        }
        long partial = store.part(12).length();
        assertTrue("kept for resume: " + partial, partial > 0 && partial < apk12.length);
        r.cutAfter = -1;
        r.ranges.clear();
        File f = engine.download(m, null);
        assertEquals("bytes=" + partial + "-", r.ranges.get(0));
        assertTrue(Arrays.equals(apk12, Files.readAllBytes(f.toPath())));
    }

    @Test
    public void startsOverWhenTheServerIgnoresRange() throws Exception {
        UpdateManifest m = manifest();
        UpdateFixture.Route r = srv.routes.get("/z4-12.apk");
        r.cutAfter = 20_000;
        try {
            engine.download(m, null);
            fail();
        } catch (UpdateException expected) {
            // interrupted
        }
        r.cutAfter = -1;
        r.supportRange = false;
        File f = engine.download(m, null);
        assertTrue(Arrays.equals(apk12, Files.readAllBytes(f.toPath())));
    }

    @Test
    public void aStalePartOfAnotherFileIsNotContinued() throws Exception {
        UpdateManifest m = manifest();
        FileOutputStream out = new FileOutputStream(store.part(12));
        out.write(new byte[5_000]); // garbage left by something else, no matching meta file
        out.close();
        File f = engine.download(m, null);
        assertTrue(Arrays.equals(apk12, Files.readAllBytes(f.toPath())));
        assertTrue(srv.routes.get("/z4-12.apk").ranges.isEmpty());
    }

    @Test
    public void aPartFromAnOlderPublishOfTheSameVersionIsNotContinued() throws Exception {
        UpdateManifest m = manifest();
        srv.routes.get("/z4-12.apk").cutAfter = 25_000;
        try {
            engine.download(m, null);
            fail();
        } catch (UpdateException expected) {
            // interrupted
        }
        byte[] republished = UpdateFixture.apk("com.z4motioncam", 12, "aa", 7);
        publish(12, republished, null);
        UpdateManifest m2 = manifest();
        File f = engine.download(m2, null);
        assertTrue(Arrays.equals(republished, Files.readAllBytes(f.toPath())));
    }

    @Test
    public void aCompletePartThatServerCalls416IsHandled() throws Exception {
        UpdateManifest m = manifest();
        engine.download(m, null);
        // The verified file is deleted but a full-size part remains (e.g. a crash before verification).
        File ready = store.ready(12);
        Files.move(ready.toPath(), store.part(12).toPath());
        File f = engine.download(m, null);
        assertTrue(Arrays.equals(apk12, Files.readAllBytes(f.toPath())));
    }

    @Test
    public void cancelStopsTheDownloadAndKeepsThePart() throws Exception {
        UpdateManifest m = manifest();
        try {
            engine.download(m, new UpdateHttp.Progress() {
                @Override public void onBytes(long done, long total) { }
                @Override public boolean cancelled() { return true; }
            });
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.CANCELLED, e.kind);
        }
        assertFalse(store.ready(12).exists());
    }

    // ---- storage ----

    @Test
    public void refusesWhenThereIsNotEnoughSpace() throws Exception {
        UpdateStore small = new UpdateStore(tmp.newFolder("small")) {
            @Override long usableBytes() { return 1_000_000; }
        };
        UpdateEngine e2 = new UpdateEngine(small, env, new UpdateHttp(null, true));
        try {
            e2.download(manifest(), null);
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.STORAGE, e.kind);
            assertTrue(e.userMessage.contains("空き容量"));
            assertTrue(e.retryable());
        }
        assertEquals(0, srv.routes.get("/z4-12.apk").hits);
    }

    // ---- identity: package, version, signature ----

    @Test
    public void aDifferentSignatureIsRefusedWithAnExplanation() throws Exception {
        byte[] foreign = UpdateFixture.apk("com.z4motioncam", 12, "bb", 3);
        publish(12, foreign, null);
        UpdateException e = expectFailure(UpdateException.Kind.SIGNATURE_MISMATCH, manifest());
        assertTrue(e.userMessage.contains("署名"));
        assertTrue(e.userMessage.contains("アンインストール"));
        assertFalse(store.ready(12).exists());
        assertEquals(-1, store.newestReady(11));
    }

    @Test
    public void anExtraSignerIsAlsoADifferentIdentity() throws Exception {
        publish(12, UpdateFixture.apk("com.z4motioncam", 12, "aa,cc", 4), null);
        expectFailure(UpdateException.Kind.SIGNATURE_MISMATCH, manifest());
    }

    @Test
    public void anUnsignedApkIsRefused() throws Exception {
        publish(12, UpdateFixture.apk("com.z4motioncam", 12, "", 5), null);
        expectFailure(UpdateException.Kind.SIGNATURE_UNREADABLE, manifest());
    }

    @Test
    public void aDifferentPackageInsideIsRefused() throws Exception {
        publish(12, UpdateFixture.apk("com.other.app", 12, "aa", 6), null);
        expectFailure(UpdateException.Kind.PACKAGE_MISMATCH, manifest());
    }

    @Test
    public void aVersionThatDiffersFromTheManifestIsRefused() throws Exception {
        publish(12, UpdateFixture.apk("com.z4motioncam", 9, "aa", 8), null); // really an old build
        expectFailure(UpdateException.Kind.VERSION_MISMATCH, manifest());
    }

    @Test
    public void downgradeIsRefusedEvenIfTheManifestSaysNewer() throws Exception {
        env.installed = 20;
        UpdateManifest m = engine.check(srv.url("/update.json"), "");
        assertNull(m);
    }

    @Test
    public void verifyReadyCatchesAFileChangedAfterTheDownload() throws Exception {
        engine.download(manifest(), null);
        assertNotNull(engine.verifyReady(12));
        FileOutputStream out = new FileOutputStream(store.ready(12), true);
        out.write(1);
        out.close();
        try {
            engine.verifyReady(12);
            fail();
        } catch (UpdateException e) {
            assertTrue(e.kind == UpdateException.Kind.BAD_FORMAT || e.kind == UpdateException.Kind.SHA_MISMATCH);
        }
        assertFalse("removed", store.ready(12).exists());
    }

    @Test
    public void verifyReadyWithNothingDownloaded() {
        try {
            engine.verifyReady(12);
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.NOTHING_READY, e.kind);
        }
    }

    @Test
    public void verifyReadyRechecksTheSignatureToo() throws Exception {
        engine.download(manifest(), null);
        env.signers = new HashSet<>(Arrays.asList("zz")); // e.g. the installed app was reinstalled with another key
        try {
            engine.verifyReady(12);
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.SIGNATURE_MISMATCH, e.kind);
        }
    }

    // ---- https only ----

    @Test
    public void plainHttpIsRefusedByTheAppConfiguration() {
        UpdateEngine strict = new UpdateEngine(store, env, new UpdateHttp(null, false));
        try {
            strict.check(srv.url("/update.json"), "");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.NOT_HTTPS, e.kind);
            assertEquals("never connected", 0, srv.routes.get("/update.json").hits);
        }
    }

    @Test
    public void aManifestCannotPointToPlainHttp() throws Exception {
        UpdateFixture tls = new UpdateFixture(true);
        try {
            tls.route("/update.json").body = ("{\"versionCode\":12,\"versionName\":\"1.12\",\"apk\":\"" + srv.url("/z4-12.apk")
                    + "\",\"sha256\":\"" + UpdateFixture.sha(apk12) + "\",\"size\":" + apk12.length + "}").getBytes();
            UpdateEngine e = new UpdateEngine(store, env, new UpdateHttp(tls.clientTls, false));
            try {
                e.check(tls.url("/update.json"), "");
                fail();
            } catch (UpdateException ex) {
                assertEquals(UpdateException.Kind.NOT_HTTPS, ex.kind);
            }
        } finally {
            tls.stop();
        }
    }

    @Test
    public void worksOverRealTlsWithATrustedCertificate() throws Exception {
        UpdateFixture tls = new UpdateFixture(true);
        try {
            tls.route("/update.json").body = UpdateFixture.manifest("com.z4motioncam", 12, "1.12", "a.apk", apk12, null);
            tls.route("/a.apk").body = apk12;
            UpdateEngine e = new UpdateEngine(store, env, new UpdateHttp(tls.clientTls, false));
            UpdateManifest m = e.check(tls.url("/update.json"), "");
            assertNotNull(m);
            assertTrue(m.apkUrl.startsWith("https://"));
            assertTrue(Arrays.equals(apk12, Files.readAllBytes(e.download(m, null).toPath())));
        } finally {
            tls.stop();
        }
    }

    @Test
    public void anUntrustedCertificateIsRefused() throws Exception {
        UpdateFixture tls = new UpdateFixture(true);
        try {
            tls.route("/update.json").body = UpdateFixture.manifest("com.z4motioncam", 12, "1.12", "a.apk", apk12, null);
            UpdateEngine e = new UpdateEngine(store, env, new UpdateHttp(null, false)); // platform trust only
            try {
                e.check(tls.url("/update.json"), "");
                fail();
            } catch (UpdateException ex) {
                assertEquals(UpdateException.Kind.NETWORK, ex.kind);
            }
        } finally {
            tls.stop();
        }
    }

    @Test
    public void followsAnHttpsRedirectButNotOneToHttp() throws Exception {
        UpdateFixture tls = new UpdateFixture(true);
        try {
            tls.route("/old.json").redirectTo = "/update.json";
            tls.route("/update.json").body = UpdateFixture.manifest("com.z4motioncam", 12, "1.12", "a.apk", apk12, null);
            UpdateEngine e = new UpdateEngine(store, env, new UpdateHttp(tls.clientTls, false));
            assertNotNull(e.check(tls.url("/old.json"), ""));
            tls.route("/evil.json").redirectTo = srv.url("/update.json");
            try {
                e.check(tls.url("/evil.json"), "");
                fail();
            } catch (UpdateException ex) {
                assertEquals(UpdateException.Kind.NOT_HTTPS, ex.kind);
            }
            assertEquals(0, srv.routes.get("/update.json").hits);
        } finally {
            tls.stop();
        }
    }

    @Test
    public void redirectLoopsStop() throws Exception {
        srv.route("/a").redirectTo = "/b";
        srv.route("/b").redirectTo = "/a";
        try {
            engine.check(srv.url("/a"), "");
            fail();
        } catch (UpdateException e) {
            assertEquals(UpdateException.Kind.NETWORK, e.kind);
        }
    }

    // ---- clean-up of leftovers ----

    @Test
    public void cleanupRemovesOldPartsAndInstalledVersions() throws Exception {
        File dir = store.dir();
        new FileOutputStream(new File(dir, "update-10.apk")).close();     // older than installed
        new FileOutputStream(new File(dir, "update-11.apk.part")).close(); // same as installed
        new FileOutputStream(new File(dir, "junk.bin")).close();
        File fresh = new File(dir, "update-13.apk.part");
        new FileOutputStream(fresh).close();
        File stale = new File(dir, "update-14.apk.part");
        new FileOutputStream(stale).close();
        assertTrue(stale.setLastModified(System.currentTimeMillis() - 4L * 24 * 3600_000L));
        store.cleanup(11, -1, System.currentTimeMillis());
        assertFalse(new File(dir, "update-10.apk").exists());
        assertFalse(new File(dir, "update-11.apk.part").exists());
        assertFalse(new File(dir, "junk.bin").exists());
        assertTrue(fresh.exists());
        assertFalse(stale.exists());
    }
}
