package com.z4motioncam;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Owns the recordings directory. Files are named {@code yyyyMMdd_HHmmss.mp4}, so name order is
 * time order; a segment being written carries an extra {@code .part} suffix until it is finalized.
 */
final class RecordingStore {
    static final Pattern NAME = Pattern.compile("\\d{8}_\\d{6}(_\\d+)?\\.mp4");
    private static final String PART = ".part";

    /** Abstracted for tests. */
    interface FreeSpace {
        long usableBytes(File dir);
    }

    private static final FreeSpace FS = new FreeSpace() {
        @Override
        public long usableBytes(File dir) {
            return dir.getUsableSpace();
        }
    };

    private final File dir;
    private final long minFreeBytes;
    private final FreeSpace freeSpace;

    RecordingStore(File dir, long minFreeBytes) {
        this(dir, minFreeBytes, FS);
    }

    RecordingStore(File dir, long minFreeBytes, FreeSpace freeSpace) {
        this.dir = dir;
        this.minFreeBytes = minFreeBytes;
        this.freeSpace = freeSpace;
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
    }

    File dir() {
        return dir;
    }

    long usableBytes() {
        return freeSpace.usableBytes(dir);
    }

    /** New in-progress file for a segment starting now. */
    File newPartFile(long nowMs) {
        String base = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date(nowMs));
        String name = base + ".mp4";
        for (int i = 1; new File(dir, name).exists() || new File(dir, name + PART).exists(); i++) {
            name = base + "_" + i + ".mp4";
        }
        return new File(dir, name + PART);
    }

    static File finalFileFor(File part) {
        String n = part.getName();
        return new File(part.getParentFile(), n.substring(0, n.length() - PART.length()));
    }

    /** Finished recordings, newest first. */
    List<File> list() {
        File[] files = dir.listFiles();
        List<File> out = new ArrayList<>();
        if (files == null) return out;
        for (File f : files) {
            if (f.isFile() && NAME.matcher(f.getName()).matches()) out.add(f);
        }
        Collections.sort(out, Collections.reverseOrder());
        return out;
    }

    /** Resolves a client-supplied name to a finished recording, or null (also blocks path traversal). */
    File find(String name) {
        if (name == null || !NAME.matcher(name).matches()) return null;
        File f = new File(dir, name);
        return f.isFile() ? f : null;
    }

    /** Removes segments left behind by a crash or power loss (unfinalized MP4s are unplayable). */
    void deleteStaleParts() {
        File[] files = dir.listFiles();
        if (files == null) return;
        for (File f : files) {
            if (f.getName().endsWith(PART)) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
            }
        }
    }

    /**
     * Deletes the oldest recordings until the free space is at least {@code minFreeBytes}.
     *
     * @return true when enough space is available afterwards
     */
    boolean ensureFreeSpace() {
        if (usableBytes() >= minFreeBytes) return true;
        File[] files = dir.listFiles();
        if (files == null) return false;
        Arrays.sort(files);
        for (File f : files) {
            if (!f.isFile() || !NAME.matcher(f.getName()).matches()) continue;
            //noinspection ResultOfMethodCallIgnored
            f.delete();
            if (usableBytes() >= minFreeBytes) return true;
        }
        return usableBytes() >= minFreeBytes;
    }
}
