package com.z4motioncam;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.regex.Pattern;

/**
 * Owns the recordings directory. Files are named {@code yyyyMMdd_HHmmss.mp4}, so name order is
 * time order; a segment being written carries an extra {@code .part} suffix until it is finalized.
 * Small JPEG thumbnails live in the hidden {@code .thumbs} folder next to them, made once per
 * recording (right after it is finished, or on first request for older recordings).
 */
final class RecordingStore {
    static final Pattern NAME = Pattern.compile("\\d{8}_\\d{6}(_\\d+)?\\.mp4");
    private static final String PART = ".part";

    /** Makes the thumbnail of a recording; abstracted because it needs Android's decoder. */
    interface ThumbMaker {
        /** Writes a small JPEG of {@code video} to {@code out}; false when it cannot. */
        boolean make(File video, File out);
    }

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
    /** Finished recordings never change, so their durations are read once. */
    private final java.util.concurrent.ConcurrentHashMap<String, Long> durations =
            new java.util.concurrent.ConcurrentHashMap<>();
    private final FreeSpace freeSpace;
    private final File thumbsDir;
    private volatile ThumbMaker thumbMaker;
    private final Object thumbLock = new Object();
    private ExecutorService thumbExecutor;

    RecordingStore(File dir, long minFreeBytes) {
        this(dir, minFreeBytes, FS);
    }

    RecordingStore(File dir, long minFreeBytes, FreeSpace freeSpace) {
        this.dir = dir;
        this.minFreeBytes = minFreeBytes;
        this.freeSpace = freeSpace;
        this.thumbsDir = new File(dir, ".thumbs");
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
    }

    void setThumbMaker(ThumbMaker maker) {
        thumbMaker = maker;
    }

    /**
     * Thumbnail of a finished recording, made now when missing (one at a time, so a page full of
     * old recordings cannot load the phone); null when there is none and it cannot be made.
     */
    File thumb(String name) {
        File video = find(name);
        if (video == null) return null;
        File out = new File(thumbsDir, name + ".jpg");
        synchronized (thumbLock) {
            if (out.isFile() && out.length() > 0) return out;
            ThumbMaker maker = thumbMaker;
            if (maker == null) return null;
            //noinspection ResultOfMethodCallIgnored
            thumbsDir.mkdirs();
            File tmp = new File(thumbsDir, name + ".tmp");
            boolean ok;
            try {
                ok = maker.make(video, tmp) && tmp.length() > 0 && tmp.renameTo(out);
            } catch (RuntimeException e) {
                ok = false;
            }
            if (!ok) {
                //noinspection ResultOfMethodCallIgnored
                tmp.delete();
                return null;
            }
            return out;
        }
    }

    /** Makes the thumbnail of a just finished recording in the background (low priority, one at a time). */
    void requestThumb(final File video) {
        if (video == null || thumbMaker == null) return;
        synchronized (thumbLock) {
            if (thumbExecutor == null) {
                thumbExecutor = Executors.newSingleThreadExecutor(new ThreadFactory() {
                    @Override
                    public Thread newThread(Runnable r) {
                        Thread t = new Thread(r, "thumbs");
                        t.setDaemon(true);
                        t.setPriority(Thread.MIN_PRIORITY);
                        return t;
                    }
                });
            }
            thumbExecutor.execute(new Runnable() {
                @Override
                public void run() {
                    thumb(video.getName());
                }
            });
        }
    }

    /** Deletes a recording together with its thumbnail. */
    boolean deleteRecording(File video) {
        File t = new File(thumbsDir, video.getName() + ".jpg");
        if (t.exists()) {
            //noinspection ResultOfMethodCallIgnored
            t.delete();
        }
        return video.delete();
    }

    /** Removes thumbnails whose recording is gone, and half-written ones. */
    void pruneThumbs() {
        File[] files = thumbsDir.listFiles();
        if (files == null) return;
        for (File f : files) {
            String n = f.getName();
            boolean orphan = !n.endsWith(".jpg") || !new File(dir, n.substring(0, n.length() - 4)).isFile();
            if (orphan) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
            }
        }
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

    /** Length of a finished recording in milliseconds, or -1. */
    long durationMs(File f) {
        String key = f.getName() + "/" + f.length();
        Long d = durations.get(key);
        if (d == null) {
            d = Mp4Info.durationMs(f);
            if (durations.size() > 5000) durations.clear();
            durations.put(key, d);
        }
        return d;
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
            deleteRecording(f);
            if (usableBytes() >= minFreeBytes) return true;
        }
        return usableBytes() >= minFreeBytes;
    }
}
