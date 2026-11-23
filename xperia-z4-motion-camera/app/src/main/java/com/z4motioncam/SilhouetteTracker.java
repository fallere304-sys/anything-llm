package com.z4motioncam;

import java.util.Arrays;

/**
 * Judges from the motion grid alone whether the moving cells look like a person: a body-sized,
 * body-shaped group that keeps moving for a few seconds. No pixels, no model; the grid is the
 * (brightness-invariant) mask of {@link MotionDetector}, so a lamp or an exposure change never
 * produces a shape.
 *
 * <p>Every analysis the mask is stamped (in upright, i.e. rotated, grid coordinates) into a
 * "last seen" grid. The union of the last {@link #UNION_MS} is the silhouette: a person who is only
 * moving an arm still leaves the whole outline over a couple of seconds, which is what makes dark
 * and backlit scenes work. The union is cut into connected groups (a one-cell gap is bridged) and
 * each group is judged by:
 * <ul>
 *   <li>size: height and bounding-box area as a share of the picture (a distant person is small,
 *       a curtain is big), width share;
 *   <li>shape: width/height (a standing person 0.2-0.9, a sitting one about 1, a lying one 1.5-4),
 *       fill of the bounding box (noise and thin streaks fill little);
 *   <li>a vertical "core": the narrow tall part of a group. A person with a floor shadow or a
 *       trailing blanket is wide as a whole but has a tall narrow core;
 *   <li>curtain tests: a wide group hanging from the top edge, and a large group whose centre swings
 *       back and forth without going anywhere (periodic sway);
 *   <li>persistence: the group must be present in several analyses within the last
 *       {@link #PERSIST_MS}, so one flash of noise is not a person.
 * </ul>
 * The three levels differ only in these limits ({@link #params(int)}). A dark picture relaxes the
 * size limits of the two looser levels (a small dark shape is all there is to see).
 */
final class SilhouetteTracker {
    static final long UNION_MS = 2_000L;
    static final long PERSIST_MS = 4_000L;
    private static final int RING = 16;

    /** Limits of one level; fractions are of the upright grid. */
    static final class Params {
        float minHeight;
        float minArea;
        float maxArea;
        float maxWidth;
        float minFill;
        float maxAspect;
        float wideMinArea;
        int minCells;
        int persist;
    }

    /** What was found, for tests and the journal. */
    static final class Shape {
        boolean person;
        float height;
        float width;
        float area;
        float aspect;
        float fill;
        boolean curtain;
        boolean sway;
        boolean viaCore;
        int persist;
        /** Closeness to the limits, 0..1 (comparable only within one level). */
        float score;
        /** Bounding box of the group in cells of the upright grid (inclusive), and that grid's size; 0 = none. */
        int minX;
        int minY;
        int maxX;
        int maxY;
        int gridW;
        int gridH;
    }

    static Params params(int level) {
        Params p = new Params();
        switch (level) {
            case 1: // strict: big, compact, clearly human proportions, seen for a while
                p.minHeight = 0.30f; p.minArea = 0.060f; p.maxArea = 0.50f; p.maxWidth = 0.45f;
                p.minFill = 0.45f; p.maxAspect = 1.3f; p.wideMinArea = 0.10f; p.minCells = 12; p.persist = 4;
                break;
            case 3: // loose: small dark shapes too (cats and curtains get through more often)
                p.minHeight = 0.12f; p.minArea = 0.012f; p.maxArea = 0.65f; p.maxWidth = 0.65f;
                p.minFill = 0.25f; p.maxAspect = 5.0f; p.wideMinArea = 0.025f; p.minCells = 6; p.persist = 2;
                break;
            default: // standard
                p.minHeight = 0.22f; p.minArea = 0.035f; p.maxArea = 0.60f; p.maxWidth = 0.55f;
                p.minFill = 0.35f; p.maxAspect = 3.5f; p.wideMinArea = 0.06f; p.minCells = 9; p.persist = 3;
                break;
        }
        return p;
    }

    private int uw;
    private int uh;
    private long[] seen = new long[0];
    private boolean[] union = new boolean[0];
    private int[] label = new int[0];
    private int[] stack = new int[0];
    private int[] colMin = new int[0];
    private int[] colMax = new int[0];
    private int[] colCnt = new int[0];
    private static final long NEVER = Long.MIN_VALUE / 2;

    private final long[] obsT = new long[RING];
    private final float[] obsCx = new float[RING];
    private final float[] obsW = new float[RING];
    private final float[] obsArea = new float[RING];
    private int obsN;
    private int obsHead;
    private long lastObserveMs = NEVER;

    long lastObserveMs() {
        return lastObserveMs;
    }

    void reset() {
        Arrays.fill(seen, NEVER);
        obsN = 0;
        obsHead = 0;
        lastObserveMs = NEVER;
    }

    private void ensure(int w, int h) {
        if (w == uw && h == uh) return;
        uw = w;
        uh = h;
        int n = w * h;
        seen = new long[n];
        Arrays.fill(seen, NEVER);
        union = new boolean[n];
        label = new int[n];
        stack = new int[n];
        colMin = new int[w];
        colMax = new int[w];
        colCnt = new int[w];
        obsN = 0;
        obsHead = 0;
    }

    /**
     * Stamps one analysis' changed cells. Cheap: nothing but a pass over the grid, and the grouping
     * runs only when something changed.
     *
     * @param rotation clockwise degrees that make the picture upright
     */
    void observe(boolean[] mask, int gw, int gh, int rotation, long now) {
        lastObserveMs = now;
        if (mask == null || gw <= 0 || gh <= 0 || mask.length < gw * gh) return;
        int rot = ((rotation % 360) + 360) % 360;
        boolean turned = rot % 180 != 0;
        ensure(turned ? gh : gw, turned ? gw : gh);
        boolean any = false;
        for (int y = 0; y < gh; y++) {
            for (int x = 0; x < gw; x++) {
                if (!mask[y * gw + x]) continue;
                int ux;
                int uy;
                switch (rot) {
                    case 90: ux = gh - 1 - y; uy = x; break;
                    case 180: ux = gw - 1 - x; uy = gh - 1 - y; break;
                    case 270: ux = y; uy = gw - 1 - x; break;
                    default: ux = x; uy = y; break;
                }
                seen[uy * uw + ux] = now;
                any = true;
            }
        }
        if (!any) return;
        // Record the biggest group of this moment for the persistence and sway tests.
        int groups = group(now);
        int best = -1;
        int bestCells = 0;
        for (int g = 0; g < groups; g++) {
            if (gCells[g] > bestCells) {
                bestCells = gCells[g];
                best = g;
            }
        }
        if (best < 0 || bestCells < 4) return;
        int i = (obsHead + obsN) % RING;
        if (obsN == RING) obsHead = (obsHead + 1) % RING;
        else obsN++;
        obsT[i] = now;
        // Centre of what moved just now inside that group (the union's centre would hide a sway).
        long sx = 0;
        int sn = 0;
        for (int k = 0; k < uw * uh; k++) {
            if (label[k] == best && seen[k] == now) {
                sx += k % uw;
                sn++;
            }
        }
        obsCx[i] = sn >= 3 ? sx / (float) sn : gSumX[best] / (float) bestCells;
        obsW[i] = gMaxX[best] - gMinX[best] + 1;
        obsArea[i] = (gMaxX[best] - gMinX[best] + 1) * (gMaxY[best] - gMinY[best] + 1) / (float) (uw * uh);
    }

    // group statistics of the last call to group()
    private int[] gCells = new int[64];
    private int[] gMinX = new int[64];
    private int[] gMaxX = new int[64];
    private int[] gMinY = new int[64];
    private int[] gMaxY = new int[64];
    private long[] gSumX = new long[64];
    private int[] gStart = new int[64];

    /** Builds the union of the last {@link #UNION_MS} and its connected groups; returns their count. */
    private int group(long now) {
        int n = uw * uh;
        boolean empty = true;
        for (int i = 0; i < n; i++) {
            boolean u = now - seen[i] <= UNION_MS;
            union[i] = u;
            if (u) empty = false;
        }
        if (empty) return 0;
        Arrays.fill(label, 0, n, -1);
        int groups = 0;
        for (int s = 0; s < n; s++) {
            if (!union[s] || label[s] >= 0) continue;
            if (groups == gCells.length) grow();
            int g = groups++;
            gCells[g] = 0;
            gMinX[g] = uw;
            gMaxX[g] = -1;
            gMinY[g] = uh;
            gMaxY[g] = -1;
            gSumX[g] = 0;
            gStart[g] = s;
            int sp = 0;
            stack[sp++] = s;
            label[s] = g;
            while (sp > 0) {
                int i = stack[--sp];
                int x = i % uw;
                int y = i / uw;
                gCells[g]++;
                gSumX[g] += x;
                if (x < gMinX[g]) gMinX[g] = x;
                if (x > gMaxX[g]) gMaxX[g] = x;
                if (y < gMinY[g]) gMinY[g] = y;
                if (y > gMaxY[g]) gMaxY[g] = y;
                // Bridge gaps of one cell: look two cells away.
                for (int dy = -2; dy <= 2; dy++) {
                    int yy = y + dy;
                    if (yy < 0 || yy >= uh) continue;
                    for (int dx = -2; dx <= 2; dx++) {
                        int xx = x + dx;
                        if (xx < 0 || xx >= uw) continue;
                        int j = yy * uw + xx;
                        if (union[j] && label[j] < 0) {
                            label[j] = g;
                            stack[sp++] = j;
                        }
                    }
                }
            }
        }
        return groups;
    }

    private void grow() {
        int m = gCells.length * 2;
        gCells = Arrays.copyOf(gCells, m);
        gMinX = Arrays.copyOf(gMinX, m);
        gMaxX = Arrays.copyOf(gMaxX, m);
        gMinY = Arrays.copyOf(gMinY, m);
        gMaxY = Arrays.copyOf(gMaxY, m);
        gSumX = Arrays.copyOf(gSumX, m);
        gStart = Arrays.copyOf(gStart, m);
    }

    /** A break longer than this ends the run measured by {@link #sustainedMs}. */
    static final long GAP_MS = 2_500L;

    /**
     * How long some group has been moving without a real break, up to the latest analysis; 0 when
     * nothing moved lately. Used to demand "keeps moving for a few seconds" when the body model could
     * not look (dark picture) and the shape alone has to decide.
     */
    long sustainedMs(long now) {
        if (obsN == 0) return 0L;
        int newest = (obsHead + obsN - 1) % RING;
        if (now - obsT[newest] > GAP_MS) return 0L;
        long since = obsT[newest];
        for (int k = obsN - 2; k >= 0; k--) {
            int i = (obsHead + k) % RING;
            int next = (obsHead + k + 1) % RING;
            if (obsT[next] - obsT[i] > GAP_MS) break;
            since = obsT[i];
        }
        return obsT[newest] - since;
    }

    /** Observations of the last {@link #PERSIST_MS}. */
    private int recentObservations(long now) {
        int c = 0;
        for (int k = 0; k < obsN; k++) if (now - obsT[(obsHead + k) % RING] <= PERSIST_MS) c++;
        return c;
    }

    /** The centre of the biggest group swings back and forth without travelling (a curtain in a draught). */
    private boolean swaying(long now) {
        int m = 0;
        float[] xs = new float[RING];
        float[] ws = new float[RING];
        for (int k = 0; k < obsN; k++) {
            int i = (obsHead + k) % RING;
            if (now - obsT[i] > PERSIST_MS + 2000L) continue;
            xs[m] = obsCx[i];
            ws[m] = obsW[i];
            m++;
        }
        if (m < 6) return false;
        int changes = 0;
        int lastSign = 0;
        float lo = xs[0];
        float hi = xs[0];
        float w = 0;
        for (int k = 1; k < m; k++) {
            float d = xs[k] - xs[k - 1];
            lo = Math.min(lo, xs[k]);
            hi = Math.max(hi, xs[k]);
            w = Math.max(w, ws[k]);
            if (Math.abs(d) < 0.25f) continue;
            int s = d > 0 ? 1 : -1;
            if (lastSign != 0 && s != lastSign) changes++;
            lastSign = s;
        }
        float travel = Math.abs(xs[m - 1] - xs[0]);
        // Reversing at least three times while ending near where it started, within about a body width.
        return changes >= 3 && travel < 0.35f * Math.max(1f, w) && (hi - lo) < 1.2f * Math.max(1f, w);
    }

    /**
     * @param level 1 strict .. 3 loose
     * @param dark  the picture is dark overall: the size limits of levels 2 and 3 are relaxed
     */
    Shape evaluate(long now, int level, boolean dark) {
        Shape best = new Shape();
        if (uw == 0 || uh == 0) return best;
        Params p = params(level);
        float sizeRelax = dark && level >= 2 ? 0.5f : 1f;
        float heightRelax = dark && level >= 2 ? 0.75f : 1f;
        int persistNow = recentObservations(now);
        best.persist = persistNow;
        int groups = group(now);
        if (groups == 0) return best;
        boolean sway = swaying(now);
        for (int g = 0; g < groups; g++) {
            Shape s = judge(g, p, sizeRelax, heightRelax, sway);
            s.persist = persistNow;
            if (persistNow < p.persist) s.person = false;
            if (s.person && (!best.person || s.score > best.score)) best = s;
            else if (!best.person && s.area > best.area) best = s;
        }
        return best;
    }

    private Shape judge(int g, Params p, float sizeRelax, float heightRelax, boolean sway) {
        Shape s = new Shape();
        int bw = gMaxX[g] - gMinX[g] + 1;
        int bh = gMaxY[g] - gMinY[g] + 1;
        s.height = bh / (float) uh;
        s.width = bw / (float) uw;
        s.area = s.height * s.width;
        s.aspect = bw / (float) bh;
        s.fill = gCells[g] / (float) (bw * bh);
        s.sway = sway;
        s.minX = gMinX[g];
        s.minY = gMinY[g];
        s.maxX = gMaxX[g];
        s.maxY = gMaxY[g];
        s.gridW = uw;
        s.gridH = uh;
        if (gCells[g] < p.minCells * sizeRelax) return s;
        if (s.area > p.maxArea || s.width > p.maxWidth) return s;
        // A tall group hanging from the top edge is a curtain (a swaying drape shows as its two edges,
        // which are thin; a person's head is not at the very top of the picture).
        if (gMinY[g] <= 1 && s.height >= 0.35f) {
            s.curtain = true;
            return s;
        }
        // A big group swinging in place is a curtain (small ones, like a waving hand, are not judged by it).
        if (sway && s.area >= 0.10f) {
            s.curtain = true;
            return s;
        }
        float minArea = p.minArea * sizeRelax;
        float minHeight = p.minHeight * heightRelax;

        // Vertical core: the columns that are nearly as tall as the tallest one.
        Arrays.fill(colMin, 0, uw, Integer.MAX_VALUE);
        Arrays.fill(colMax, 0, uw, -1);
        Arrays.fill(colCnt, 0, uw, 0);
        int n = uw * uh;
        for (int i = 0; i < n; i++) {
            if (label[i] != g) continue;
            int x = i % uw;
            int y = i / uw;
            if (y < colMin[x]) colMin[x] = y;
            if (y > colMax[x]) colMax[x] = y;
            colCnt[x]++;
        }
        int bestX = gMinX[g];
        int bestExt = 0;
        for (int x = gMinX[g]; x <= gMaxX[g]; x++) {
            int ext = colMax[x] < 0 ? 0 : colMax[x] - colMin[x] + 1;
            if (ext > bestExt) {
                bestExt = ext;
                bestX = x;
            }
        }
        int coreL = bestX;
        int coreR = bestX;
        while (coreL > gMinX[g] && extent(coreL - 1) >= 0.6f * bestExt) coreL--;
        while (coreR < gMaxX[g] && extent(coreR + 1) >= 0.6f * bestExt) coreR++;
        int coreW = coreR - coreL + 1;
        float coreH = bestExt / (float) uh;

        boolean ok = false;
        if (s.fill >= p.minFill && s.area >= minArea && s.aspect <= p.maxAspect && s.aspect >= 0.12f) {
            if (s.aspect <= 1.1f) ok = s.height >= minHeight; // standing or sitting
            else ok = s.area >= p.wideMinArea * sizeRelax; // lying or crouching: needs more body
        }
        // A tall narrow core with a long shadow or trail beside it: still a standing person.
        if (!ok && coreH >= minHeight && coreW >= 2 && coreW <= 0.9f * bestExt
                && s.fill >= p.minFill * 0.6f && s.area >= minArea && s.width <= p.maxWidth) {
            ok = true;
            s.viaCore = true;
        }
        s.person = ok;
        if (ok) {
            float a = Math.min(1f, s.area / Math.max(minArea, 1e-3f) / 3f);
            s.score = 0.5f * a + 0.5f * Math.min(1f, s.fill);
        }
        return s;
    }

    private int extent(int x) {
        return colMax[x] < 0 ? 0 : colMax[x] - colMin[x] + 1;
    }
}
