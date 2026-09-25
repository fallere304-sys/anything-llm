package com.z4motioncam;

/**
 * Hands the latest JPEG from the camera thread to viewers. JPEGs are only produced while someone
 * is watching ({@link #isWanted}), so an idle monitor never pays for JPEG compression.
 */
final class FrameHub {
    static final class Frame {
        final byte[] jpeg;
        final long seq;

        Frame(byte[] jpeg, long seq) {
            this.jpeg = jpeg;
            this.seq = seq;
        }
    }

    /** A snapshot request keeps frames flowing for this long. */
    private static final long REQUEST_HOLD_MS = 2_000L;

    private byte[] jpeg;
    private long seq;
    private int clients;
    private long requestedAt = Long.MIN_VALUE / 2;
    private boolean closed;

    synchronized void publish(byte[] data) {
        jpeg = data;
        seq++;
        notifyAll();
    }

    synchronized void addClient() {
        clients++;
    }

    synchronized void removeClient() {
        if (clients > 0) clients--;
    }

    synchronized int clients() {
        return clients;
    }

    /** Asks for frames for a short while without holding a long-lived client slot. */
    synchronized void request(long nowMs) {
        requestedAt = nowMs;
    }

    synchronized boolean isWanted(long nowMs) {
        return clients > 0 || nowMs - requestedAt < REQUEST_HOLD_MS;
    }

    synchronized Frame latest() {
        return jpeg == null ? null : new Frame(jpeg, seq);
    }

    /** Waits for a frame newer than {@code afterSeq}; returns null on timeout or close. */
    synchronized Frame await(long afterSeq, long timeoutMs) throws InterruptedException {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (!closed && (jpeg == null || seq <= afterSeq)) {
            long left = deadline - System.currentTimeMillis();
            if (left <= 0) return null;
            wait(left);
        }
        return closed ? null : new Frame(jpeg, seq);
    }

    /** Drops the cached frame (e.g. when the camera stops) so viewers do not see a frozen image. */
    synchronized void clear() {
        jpeg = null;
    }

    synchronized void close() {
        closed = true;
        notifyAll();
    }
}
