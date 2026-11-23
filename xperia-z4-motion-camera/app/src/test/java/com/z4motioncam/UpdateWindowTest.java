package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import com.z4motioncam.UpdateWindow.Check;
import java.util.Random;
import org.junit.Test;

public class UpdateWindowTest {
    private static UpdateWindow window() {
        return new UpdateWindow(new Random(7));
    }

    @Test
    public void closedUntilOpened() {
        UpdateWindow w = window();
        assertFalse(w.isOpen(0));
        assertNull(w.code(0));
        assertEquals(Check.CLOSED, w.check("123456", 0));
        assertEquals(0, w.remainingMs(0));
    }

    @Test
    public void codeIsSixDigits() {
        for (int seed = 0; seed < 50; seed++) {
            String code = new UpdateWindow(new Random(seed)).open(0);
            assertTrue(code, code.matches("[0-9]{6}"));
        }
    }

    @Test
    public void smallNumbersKeepLeadingZeros() {
        UpdateWindow w = new UpdateWindow(new Random() {
            @Override
            public int nextInt(int bound) {
                return 42;
            }
        });
        assertEquals("000042", w.open(0));
    }

    @Test
    public void rightCodeIsAccepted() {
        UpdateWindow w = window();
        String code = w.open(1000);
        assertEquals(Check.OK, w.check(code, 2000));
        assertEquals(code, w.code(2000));
    }

    @Test
    public void whitespaceAroundTheCodeIsIgnored() {
        UpdateWindow w = window();
        String code = w.open(0);
        assertEquals(Check.OK, w.check("  " + code + "\n", 1));
    }

    @Test
    public void wrongOrMissingCodeIsRejected() {
        UpdateWindow w = window();
        String code = w.open(0);
        String other = code.equals("000000") ? "000001" : "000000";
        assertEquals(Check.WRONG, w.check(other, 1));
        assertEquals(Check.WRONG, w.check(null, 1));
        assertEquals(Check.WRONG, w.check("", 1));
        assertEquals(Check.WRONG, w.check(code + "0", 1));
        assertEquals(1, w.wrongLeft());
    }

    @Test
    public void fifthWrongCodeClosesTheWindow() {
        UpdateWindow w = window();
        String code = w.open(0);
        String bad = code.equals("999999") ? "999998" : "999999";
        for (int i = 1; i < UpdateWindow.MAX_WRONG; i++) {
            assertEquals(Check.WRONG, w.check(bad, i));
            assertEquals(UpdateWindow.MAX_WRONG - i, w.wrongLeft());
        }
        assertEquals(Check.LOCKED, w.check(bad, 10));
        assertFalse(w.isOpen(11));
        assertEquals(Check.CLOSED, w.check(code, 12)); // even the right code no longer works
    }

    @Test
    public void rightCodeBetweenWrongOnesStillWorks() {
        UpdateWindow w = window();
        String code = w.open(0);
        String bad = code.equals("999999") ? "999998" : "999999";
        w.check(bad, 1);
        w.check(bad, 2);
        assertEquals(Check.OK, w.check(code, 3));
    }

    @Test
    public void expiresAfterTenMinutes() {
        UpdateWindow w = window();
        String code = w.open(5_000);
        assertTrue(w.isOpen(5_000 + UpdateWindow.OPEN_MS - 1));
        assertEquals(Check.OK, w.check(code, 5_000 + UpdateWindow.OPEN_MS - 1));
        assertFalse(w.isOpen(5_000 + UpdateWindow.OPEN_MS));
        assertEquals(Check.CLOSED, w.check(code, 5_000 + UpdateWindow.OPEN_MS));
        assertNull(w.code(5_000 + UpdateWindow.OPEN_MS));
    }

    @Test
    public void remainingTimeCountsDown() {
        UpdateWindow w = window();
        w.open(1_000);
        assertEquals(UpdateWindow.OPEN_MS, w.remainingMs(1_000));
        assertEquals(UpdateWindow.OPEN_MS - 60_000, w.remainingMs(61_000));
    }

    @Test
    public void closeEndsItImmediately() {
        UpdateWindow w = window();
        String code = w.open(0);
        w.close();
        assertFalse(w.isOpen(1));
        assertEquals(Check.CLOSED, w.check(code, 1));
    }

    @Test
    public void reopeningGivesANewCodeAndResetsTheWrongCount() {
        UpdateWindow w = window();
        String first = w.open(0);
        String bad = first.equals("999999") ? "999998" : "999999";
        for (int i = 0; i < 4; i++) w.check(bad, i);
        assertEquals(1, w.wrongLeft());
        String second = w.open(100);
        assertEquals(UpdateWindow.MAX_WRONG, w.wrongLeft());
        assertEquals(Check.OK, w.check(second, 101));
        if (!first.equals(second)) assertNotEquals(Check.OK, w.check(first, 102));
    }

    @Test
    public void secureRandomCodesDiffer() {
        UpdateWindow w = new UpdateWindow(new java.security.SecureRandom());
        java.util.Set<String> codes = new java.util.HashSet<>();
        for (int i = 0; i < 20; i++) codes.add(w.open(i));
        assertTrue(codes.size() > 15);
    }
}
