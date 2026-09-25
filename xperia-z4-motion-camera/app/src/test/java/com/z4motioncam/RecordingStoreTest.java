package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.List;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class RecordingStoreTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    /** Pretends the disk has {@code capacity} bytes minus what our files use. */
    private static RecordingStore.FreeSpace fakeDisk(final long capacity) {
        return new RecordingStore.FreeSpace() {
            @Override
            public long usableBytes(File dir) {
                long used = 0;
                File[] files = dir.listFiles();
                if (files != null) for (File f : files) used += f.length();
                return capacity - used;
            }
        };
    }

    private static File write(File dir, String name, int size) throws IOException {
        File f = new File(dir, name);
        FileOutputStream out = new FileOutputStream(f);
        out.write(new byte[size]);
        out.close();
        return f;
    }

    @Test
    public void deletesOldestUntilEnoughSpace() throws IOException {
        File dir = tmp.newFolder("rec");
        write(dir, "20260101_000000.mp4", 400);
        write(dir, "20260102_000000.mp4", 400);
        write(dir, "20260103_000000.mp4", 400);
        File part = write(dir, "20260104_000000.mp4.part", 100);
        // capacity 1500, used 1300 -> free 200; need 500
        RecordingStore store = new RecordingStore(dir, 500, fakeDisk(1500));
        assertTrue(store.ensureFreeSpace());
        assertFalse(new File(dir, "20260101_000000.mp4").exists());
        assertTrue(new File(dir, "20260102_000000.mp4").exists());
        assertTrue("segment being written is kept", part.exists());
    }

    @Test
    public void reportsFailureWhenNothingLeftToDelete() throws IOException {
        File dir = tmp.newFolder("rec");
        write(dir, "20260101_000000.mp4", 100);
        write(dir, "other.bin", 900); // not ours: never deleted
        RecordingStore store = new RecordingStore(dir, 500, fakeDisk(1200));
        assertFalse(store.ensureFreeSpace());
        assertTrue(new File(dir, "other.bin").exists());
    }

    @Test
    public void listsNewestFirstAndOnlyFinishedFiles() throws IOException {
        File dir = tmp.newFolder("rec");
        write(dir, "20260101_000000.mp4", 1);
        write(dir, "20260103_000000.mp4", 1);
        write(dir, "20260102_000000.mp4.part", 1);
        write(dir, "20260102_000000.mp4", 1);
        List<File> l = new RecordingStore(dir, 0, fakeDisk(100)).list();
        assertEquals(3, l.size());
        assertEquals("20260103_000000.mp4", l.get(0).getName());
        assertEquals("20260101_000000.mp4", l.get(2).getName());
    }

    @Test
    public void findRejectsTraversal() throws IOException {
        File dir = tmp.newFolder("rec");
        write(dir, "20260101_000000.mp4", 1);
        RecordingStore store = new RecordingStore(dir, 0, fakeDisk(100));
        assertEquals("20260101_000000.mp4", store.find("20260101_000000.mp4").getName());
        assertNull(store.find("../secret.mp4"));
        assertNull(store.find("20260101_000000.mp4.part"));
        assertNull(store.find(null));
    }

    @Test
    public void partFileNamesAreUniqueAndFinalize() throws IOException {
        File dir = tmp.newFolder("rec");
        RecordingStore store = new RecordingStore(dir, 0, fakeDisk(100));
        File a = store.newPartFile(0L);
        assertTrue(a.getName().endsWith(".mp4.part"));
        assertTrue(a.createNewFile());
        File b = store.newPartFile(0L);
        assertFalse(a.getName().equals(b.getName()));
        File fin = RecordingStore.finalFileFor(b);
        assertTrue(RecordingStore.NAME.matcher(fin.getName()).matches());
        store.deleteStaleParts();
        assertFalse(a.exists());
    }
}
