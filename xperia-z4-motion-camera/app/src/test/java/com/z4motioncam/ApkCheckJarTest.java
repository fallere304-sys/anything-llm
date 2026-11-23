package com.z4motioncam;

import static com.z4motioncam.UpdatePushFixture.fx;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.Random;
import java.util.Set;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/** The JAR-signature part of ApkCheck, on APKs signed with throw-away test keys. */
public class ApkCheckJarTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    @Test
    public void sameKeySameSignerDifferentKeyDifferentSigner() throws Exception {
        Set<String> a = ApkCheck.jarSigners(fx("signed-a.apk"));
        Set<String> a2 = ApkCheck.jarSigners(fx("signed-a2.apk"));
        Set<String> b = ApkCheck.jarSigners(fx("signed-b.apk"));
        assertEquals(1, a.size());
        assertEquals(64, a.iterator().next().length());
        assertEquals(a, a2);
        assertNotEquals(a, b);
    }

    @Test
    public void anUnsignedApkHasNoSigners() throws Exception {
        assertTrue(ApkCheck.jarSigners(fx("unsigned.apk")).isEmpty());
    }

    @Test
    public void changedContentIsCaughtByReadingToTheEnd() throws Exception {
        File f = tmp.newFile();
        byte[] other = new byte[30000];
        new Random(1).nextBytes(other);
        UpdatePushFixture.rewrite("signed-a.apk", f, "classes.dex", other, null);
        try {
            ApkCheck.jarSigners(f);
            fail("tampering not detected");
        } catch (SecurityException expected) {
            // good
        }
    }

    @Test
    public void aFileAddedAfterSigningIsCaught() throws Exception {
        File f = tmp.newFile();
        UpdatePushFixture.rewrite("signed-a.apk", f, null, null, "evil.dex");
        try {
            ApkCheck.jarSigners(f);
            fail("added file not detected");
        } catch (SecurityException expected) {
            // good
        }
    }

    @Test
    public void aTruncatedApkIsAnIoError() throws Exception {
        byte[] all = UpdatePushFixture.bytes(fx("signed-a.apk"));
        File f = tmp.newFile();
        FileOutputStream out = new FileOutputStream(f);
        out.write(all, 0, all.length - 1500); // loses the central directory
        out.close();
        try {
            ApkCheck.jarSigners(f);
            fail("expected an error");
        } catch (IOException expected) {
            // good
        }
    }

    @Test
    public void theCheckDoesNotModifyTheFile() throws Exception {
        String before = ApkCheck.sha256Hex(fx("signed-a2.apk"));
        ApkCheck.jarSigners(fx("signed-a2.apk"));
        assertEquals(before, ApkCheck.sha256Hex(fx("signed-a2.apk")));
        assertFalse(before.isEmpty());
    }
}
