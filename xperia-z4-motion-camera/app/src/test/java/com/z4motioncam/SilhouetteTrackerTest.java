package com.z4motioncam;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** The shape rules on hand-made motion grids (48 x 36). */
public class SilhouetteTrackerTest {
    private static final int GW = 48;
    private static final int GH = 36;

    private static boolean[] rect(int x, int y, int w, int h) {
        boolean[] m = new boolean[GW * GH];
        for (int j = y; j < y + h; j++) for (int i = x; i < x + w; i++) m[j * GW + i] = true;
        return m;
    }

    private static void or(boolean[] a, boolean[] b) {
        for (int i = 0; i < a.length; i++) a[i] |= b[i];
    }

    private static SilhouetteTracker feed(boolean[] m, int n, int rot) {
        SilhouetteTracker t = new SilhouetteTracker();
        for (int i = 0; i < n; i++) t.observe(m, GW, GH, rot, 1000 + i * 350L);
        return t;
    }

    private static long end(int n) {
        return 1000 + (n - 1) * 350L;
    }

    @Test public void standingPersonSizes() {
        // 9% x 39% (dark doorway sample) and 28% x 66% (backlit sample), in cells.
        boolean[] small = rect(20, 18, 4, 14);
        boolean[] big = rect(30, 8, 13, 24);
        assertTrue(feed(small, 6, 0).evaluate(end(6), 3, true).person);
        assertTrue(feed(small, 6, 0).evaluate(end(6), 2, true).person);
        assertFalse("strict wants a bigger person", feed(small, 6, 0).evaluate(end(6), 1, true).person);
        for (int l = 1; l <= 3; l++) assertTrue("big, level " + l, feed(big, 6, 0).evaluate(end(6), l, false).person);
    }

    @Test public void oneFlashIsNotEnough() {
        SilhouetteTracker t = feed(rect(20, 8, 8, 20), 1, 0);
        for (int l = 1; l <= 3; l++) assertFalse("level " + l, t.evaluate(end(1), l, false).person);
    }

    @Test public void motionLongAgoIsForgotten() {
        SilhouetteTracker t = feed(rect(20, 8, 8, 20), 6, 0);
        assertTrue(t.evaluate(end(6), 2, false).person);
        assertFalse(t.evaluate(end(6) + 6000, 2, false).person);
    }

    @Test public void hangingStripIsACurtain() {
        SilhouetteTracker t = feed(rect(30, 0, 3, 22), 8, 0);
        for (int l = 1; l <= 3; l++) assertFalse("level " + l, t.evaluate(end(8), l, false).person);
        assertTrue(t.evaluate(end(8), 3, false).curtain);
    }

    @Test public void aWholeWallOfMotionIsNot() {
        SilhouetteTracker t = feed(rect(2, 2, 44, 32), 8, 0);
        for (int l = 1; l <= 3; l++) assertFalse(t.evaluate(end(8), l, false).person);
    }

    @Test public void swayingInPlaceIsACurtain() {
        SilhouetteTracker t = new SilhouetteTracker();
        for (int i = 0; i < 12; i++) {
            int x = 14 + ((i % 2 == 0) ? 0 : 3);
            boolean[] m = rect(x, 10, 10, 20); // floor-length drape, not touching the top
            t.observe(m, GW, GH, 0, 1000 + i * 350L);
        }
        for (int l = 1; l <= 3; l++) assertFalse("level " + l, t.evaluate(end(12), l, false).person);
    }

    @Test public void aWalkingPersonIsNotSway() {
        SilhouetteTracker t = new SilhouetteTracker();
        for (int i = 0; i < 12; i++) t.observe(rect(6 + i * 3, 10, 8, 20), GW, GH, 0, 1000 + i * 350L);
        assertTrue(t.evaluate(end(12), 2, false).person);
    }

    @Test public void floorShadowDoesNotHideAStandingPerson() {
        boolean[] m = rect(20, 8, 6, 22);
        or(m, rect(20, 28, 20, 2)); // long shadow on the floor
        SilhouetteTracker t = feed(m, 6, 0);
        SilhouetteTracker.Shape s = t.evaluate(end(6), 2, false);
        assertTrue("aspect " + s.aspect + " fill " + s.fill, s.person);
    }

    @Test public void lyingAndSittingShapes() {
        assertTrue(feed(rect(12, 24, 18, 6), 6, 0).evaluate(end(6), 2, false).person); // lying
        assertTrue(feed(rect(14, 20, 10, 10), 6, 0).evaluate(end(6), 2, false).person); // sitting
        assertFalse("strict: lying is not accepted", feed(rect(12, 24, 18, 6), 6, 0).evaluate(end(6), 1, false).person);
    }

    @Test public void smallLowThingsAreOnlyForTheLooseLevel() {
        boolean[] cat = rect(20, 22, 5, 8);
        assertFalse(feed(cat, 6, 0).evaluate(end(6), 1, false).person);
        assertFalse(feed(cat, 6, 0).evaluate(end(6), 2, false).person);
        assertTrue(feed(cat, 6, 0).evaluate(end(6), 3, false).person);
    }

    @Test public void scatteredNoiseIsNot() {
        boolean[] m = new boolean[GW * GH];
        for (int i = 0; i < 40; i++) m[(i * 37) % m.length] = true;
        for (int l = 1; l <= 3; l++) assertFalse(feed(m, 8, 0).evaluate(end(8), l, true).person);
    }

    @Test public void rotationUsesUprightCoordinates() {
        boolean[] lying = rect(10, 14, 20, 6); // wide in the raw grid
        assertFalse(feed(lying, 6, 0).evaluate(end(6), 1, false).person);
        assertTrue(feed(lying, 6, 90).evaluate(end(6), 1, false).person);
        assertTrue(feed(lying, 6, 270).evaluate(end(6), 1, false).person);
        assertFalse(feed(lying, 6, 180).evaluate(end(6), 1, false).person);
    }

    @Test public void levelsAreNested() {
        boolean[][] cases = {rect(20, 18, 4, 14), rect(30, 8, 13, 24), rect(12, 24, 18, 6), rect(20, 22, 5, 8), rect(14, 20, 10, 10)};
        for (boolean[] m : cases) {
            SilhouetteTracker t = feed(m, 8, 0);
            boolean s1 = t.evaluate(end(8), 1, false).person;
            boolean s2 = t.evaluate(end(8), 2, false).person;
            boolean s3 = t.evaluate(end(8), 3, false).person;
            assertTrue(!s1 || s2);
            assertTrue(!s2 || s3);
        }
    }

    @Test public void malformedInputIsHarmless() {
        SilhouetteTracker t = new SilhouetteTracker();
        t.observe(null, GW, GH, 0, 0);
        t.observe(new boolean[3], GW, GH, 0, 0);
        t.observe(new boolean[GW * GH], 0, 0, 0, 0);
        assertFalse(t.evaluate(10, 2, false).person);
    }

    @Test public void sizeChangeResetsHistory() {
        SilhouetteTracker t = feed(rect(20, 8, 8, 20), 6, 0);
        t.observe(new boolean[24 * 18], 24, 18, 0, end(6) + 100);
        assertFalse(t.evaluate(end(6) + 100, 2, false).person);
    }
}
