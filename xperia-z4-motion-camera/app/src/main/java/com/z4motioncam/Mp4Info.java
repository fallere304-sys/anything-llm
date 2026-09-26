package com.z4motioncam;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;

/** Reads the duration from an MP4's moov/mvhd box (a few small reads; no decoding). */
final class Mp4Info {
    private static final int MOOV = 0x6d6f6f76; // "moov"
    private static final int MVHD = 0x6d766864; // "mvhd"

    private Mp4Info() {}

    /** Duration in milliseconds, or -1 when it cannot be read. */
    static long durationMs(File f) {
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(f, "r");
            long len = raf.length();
            long[] moov = findBox(raf, 0, len, MOOV);
            if (moov == null) return -1;
            long[] mvhd = findBox(raf, moov[0], moov[1], MVHD);
            if (mvhd == null) return -1;
            raf.seek(mvhd[0]);
            int version = raf.readInt() >>> 24;
            long timescale;
            long duration;
            if (version == 1) {
                raf.skipBytes(16); // creation + modification time (64-bit)
                timescale = raf.readInt() & 0xFFFFFFFFL;
                duration = raf.readLong();
            } else {
                raf.skipBytes(8);
                timescale = raf.readInt() & 0xFFFFFFFFL;
                duration = raf.readInt() & 0xFFFFFFFFL;
            }
            return timescale == 0 || duration < 0 ? -1 : duration * 1000 / timescale;
        } catch (IOException e) {
            return -1;
        } finally {
            if (raf != null) {
                try {
                    raf.close();
                } catch (IOException ignored) {
                    // ignore
                }
            }
        }
    }

    /** Searches boxes in [start, end) for {@code type}; returns {payloadStart, payloadEnd} or null. */
    private static long[] findBox(RandomAccessFile raf, long start, long end, int type) throws IOException {
        long pos = start;
        while (pos + 8 <= end) {
            raf.seek(pos);
            long size = raf.readInt() & 0xFFFFFFFFL;
            int t = raf.readInt();
            int header = 8;
            if (size == 1) {
                size = raf.readLong();
                header = 16;
            } else if (size == 0) {
                size = end - pos;
            }
            if (size < header || pos + size > end) return null;
            if (t == type) return new long[] {pos + header, pos + size};
            pos += size;
        }
        return null;
    }
}
