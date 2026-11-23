package com.z4motioncam;

import java.nio.charset.Charset;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.Random;

/**
 * The "accepting an update" window that someone standing at the phone opens. While it is open, a
 * six-digit one-time code is shown on the phone's own screen, and an upload must carry that code.
 * So only a person who can see the phone (not just anyone on the Wi-Fi) can push an update. Pure
 * Java with injected time, so it is unit-tested.
 *
 * <p>The window closes by itself after {@link #OPEN_MS}, after {@link #MAX_WRONG} wrong codes, and
 * once an upload has been accepted (the code is single-use).
 */
final class UpdateWindow {
    static final long OPEN_MS = 10 * 60_000L;
    static final int MAX_WRONG = 5;

    enum Check { OK, CLOSED, WRONG, LOCKED }

    private static final Charset UTF8 = Charset.forName("UTF-8");

    private final Random random;
    private String code;
    private long closesAtMs;
    private int wrong;

    /** @param random a SecureRandom in the app; a seeded Random in tests */
    UpdateWindow(Random random) {
        this.random = random;
    }

    /** Opens (or re-opens) the window with a fresh code and returns it. */
    synchronized String open(long now) {
        code = String.format(Locale.US, "%06d", random.nextInt(1_000_000));
        closesAtMs = now + OPEN_MS;
        wrong = 0;
        return code;
    }

    synchronized boolean isOpen(long now) {
        return code != null && now < closesAtMs;
    }

    /** The code to show on the phone, or null when the window is closed. */
    synchronized String code(long now) {
        return isOpen(now) ? code : null;
    }

    synchronized long remainingMs(long now) {
        return isOpen(now) ? closesAtMs - now : 0;
    }

    synchronized int wrongLeft() {
        return Math.max(0, MAX_WRONG - wrong);
    }

    synchronized void close() {
        code = null;
    }

    /** Checks a submitted code. A wrong one counts; the {@link #MAX_WRONG}th closes the window. */
    synchronized Check check(String submitted, long now) {
        if (!isOpen(now)) return Check.CLOSED;
        String s = submitted == null ? "" : submitted.trim();
        // Constant-time comparison; the code is short, but there is no reason to leak it.
        if (MessageDigest.isEqual(s.getBytes(UTF8), code.getBytes(UTF8))) return Check.OK;
        if (++wrong >= MAX_WRONG) {
            code = null;
            return Check.LOCKED;
        }
        return Check.WRONG;
    }
}
