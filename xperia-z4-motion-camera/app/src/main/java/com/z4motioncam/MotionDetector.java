package com.z4motioncam;

import java.util.Arrays;

/**
 * Brightness-invariant motion detector working on the luma plane of NV21 frames.
 *
 * <p>The frame is cut into a coarse grid (about 48 columns). Each cell is sampled on a 4x4 lattice
 * (every sample is the mean of two neighbouring pixels, which halves sensor noise). The cell's
 * pattern is compared with a slowly adapting background pattern by <b>normalised correlation</b>
 * (the zero-mean, unit-variance structure), not by brightness difference. A pattern that is only
 * scaled and shifted in brightness (light on/off, auto exposure, gamma, a vignette, a sun patch
 * with soft edges) keeps correlation close to 1, so it is not motion. An object that moves changes
 * the pattern of the cells it crosses (its edges), so correlation drops.
 *
 * <p>Rules that make this safe:
 * <ul>
 *   <li>A cell counts only if one of the two patterns has real texture (variance clearly above the
 *       sensor noise, which is estimated every frame as the median residual of all cells). Flat
 *       against flat is never motion, whatever the brightness.
 *   <li>Cells whose samples are clipped (saturated or crushed to black) are ignored: exposure
 *       changes flatten them without anything moving.
 *   <li>Flat cells between two changed cells of the same row are filled in (a plain dark coat has
 *       no texture inside, only edges); isolated cells are dropped (noise).
 *   <li>If a large share of the grid changes with one sign of brightness shift (lights switched
 *       on or off in a dark room reveal or hide texture everywhere), the frame is an illumination
 *       event: no motion, the background is re-seeded.
 * </ul>
 * Cost: about 55k pixel reads and a few thousand float operations per analysis, at most 3 times a
 * second.
 */
final class MotionDetector {
    /** Samples per cell side. */
    private static final int S = 4;
    private static final int N = S * S;
    /** Correlation (squared) below which a textured cell counts as changed. */
    private static final float RHO2_MAX = 0.70f;
    /** Variance (luma^2) below which a pattern is "flat". */
    private static final float MIN_VAR = 4f;
    /** A cell's residual std must exceed noise * K + C to count. */
    private static final float NOISE_K = 2.0f;
    private static final float NOISE_C = 1.0f;
    private static final float NOISE_CAP = 14f;
    private static final float ILLUM_RATIO = 0.35f;
    private static final float ILLUM_COHERENCE = 0.90f;
    private static final float ILLUM_ALWAYS = 0.85f;
    private static final float GLOBAL_SHIFT_RATIO = 0.6f;
    private static final int ILLUM_SUPPRESS = 2;
    /** Consecutive analyses over the threshold required to trigger (filters sensor noise). */
    private static final int REQUIRED_HITS = 2;
    private static final int MAX_GAP_V = 2;
    /** A cell that stays changed this many analyses in a row is the new background (re-seeded). */
    private static final int HOLD_ANALYSES = 12;

    private final int gridW;
    private final int gridH;
    private final int cells;
    private final float[] cur;
    private final float[] bg;
    private final float[] meanShift;
    private final float[] resid;
    private final float[] rho2;
    private final boolean[] flat;
    private final float[] varMax;
    private final int[] age;
    private final boolean[] valid;
    private final boolean[] raw;
    private final boolean[] changedMask;
    private final int[] noiseHist = new int[64];
    private int[] xs = new int[0];
    private int[] ys = new int[0];
    private int sizeW;
    private int sizeH;
    private boolean initialized;
    private int hits;
    private int suppress;
    private float noise = -1f;
    private float lastRatio;
    private boolean lastIllumination;

    MotionDetector(int gridW, int gridH) {
        this.gridW = gridW;
        this.gridH = gridH;
        this.cells = gridW * gridH;
        this.cur = new float[cells * N];
        this.bg = new float[cells * N];
        this.meanShift = new float[cells];
        this.resid = new float[cells];
        this.rho2 = new float[cells];
        this.flat = new boolean[cells];
        this.varMax = new float[cells];
        this.age = new int[cells];
        this.valid = new boolean[cells];
        this.raw = new boolean[cells];
        this.changedMask = new boolean[cells];
    }

    /** Grid of roughly 48 columns with the frame's aspect ratio. */
    static MotionDetector forFrame(int width, int height) {
        int gw = 48;
        int gh = Math.max(1, Math.round(gw * (float) height / width));
        return new MotionDetector(gw, gh);
    }

    float lastRatio() {
        return lastRatio;
    }

    int gridW() {
        return gridW;
    }

    int gridH() {
        return gridH;
    }

    /** True when the last analysis was judged a lighting change (nothing moved). */
    boolean lastWasIllumination() {
        return lastIllumination;
    }

    /** Estimated sensor noise (luma std) of the last analysis. */
    float noiseStd() {
        return Math.max(0f, noise);
    }

    /** Changed cells of the last analysis; reused between calls, read it right away. */
    boolean[] changedMask() {
        return changedMask;
    }

    void reset() {
        initialized = false;
        hits = 0;
        suppress = 0;
        noise = -1f;
        lastRatio = 0f;
        lastIllumination = false;
        Arrays.fill(changedMask, false);
        Arrays.fill(age, 0);
    }

    /**
     * Analyses one frame and returns true when motion is detected.
     *
     * @param areaThreshold fraction of cells (0..1) that must change to count as motion
     */
    boolean update(byte[] nv21, int width, int height, float areaThreshold) {
        float ratio = analyze(nv21, width, height);
        if (ratio >= areaThreshold) {
            hits++;
        } else {
            hits = 0;
        }
        return hits >= REQUIRED_HITS;
    }

    private void prepare(int width, int height) {
        if (width == sizeW && height == sizeH && xs.length == gridW * S) return;
        sizeW = width;
        sizeH = height;
        xs = new int[gridW * S];
        ys = new int[gridH * S];
        int cw = Math.max(1, width / gridW);
        int ch = Math.max(1, height / gridH);
        for (int g = 0; g < gridW; g++) {
            for (int s = 0; s < S; s++) xs[g * S + s] = Math.min(width - 2, g * cw + (2 * s + 1) * cw / (2 * S));
        }
        for (int g = 0; g < gridH; g++) {
            for (int s = 0; s < S; s++) ys[g * S + s] = Math.min(height - 1, g * ch + (2 * s + 1) * ch / (2 * S));
        }
        initialized = false;
    }

    /** Returns the fraction of grid cells that changed structurally (0..1). */
    float analyze(byte[] nv21, int width, int height) {
        prepare(width, height);
        // 1. Sample the lattice.
        for (int gy = 0; gy < gridH; gy++) {
            for (int sy = 0; sy < S; sy++) {
                int row = ys[gy * S + sy] * width;
                for (int gx = 0; gx < gridW; gx++) {
                    int base = (gy * gridW + gx) * N + sy * S;
                    for (int sx = 0; sx < S; sx++) {
                        int p = row + xs[gx * S + sx];
                        cur[base + sx] = ((nv21[p] & 0xFF) + (nv21[p + 1] & 0xFF)) * 0.5f;
                    }
                }
            }
        }
        lastIllumination = false;
        if (!initialized) {
            System.arraycopy(cur, 0, bg, 0, cur.length);
            initialized = true;
            lastRatio = 0f;
            Arrays.fill(changedMask, false);
            return 0f;
        }
        if (suppress > 0) {
            suppress--;
            System.arraycopy(cur, 0, bg, 0, cur.length);
            lastRatio = 0f;
            lastIllumination = true;
            Arrays.fill(changedMask, false);
            return 0f;
        }

        // 2. Per-cell statistics: correlation of the pattern with the background pattern.
        Arrays.fill(noiseHist, 0);
        int gUp = 0;
        int gDown = 0;
        for (int i = 0; i < cells; i++) {
            int o = i * N;
            float sc = 0;
            float sb = 0;
            int hiC = 0;
            int hiB = 0;
            int loC = 0;
            int loB = 0;
            for (int k = 0; k < N; k++) {
                float c = cur[o + k];
                float b = bg[o + k];
                sc += c;
                sb += b;
                if (c >= 250f) hiC++;
                else if (c <= 2f) loC++;
                if (b >= 250f) hiB++;
                else if (b <= 2f) loB++;
            }
            float mc = sc / N;
            float mb = sb / N;
            // Remove the best-fit plane of each patch (a soft light gradient, a vignette, the edge of a
            // sun patch); what is left is the structure that is compared.
            float cu = 0;
            float cv = 0;
            float bu = 0;
            float bv = 0;
            for (int k = 0; k < N; k++) {
                float u = (k & 3) - 1.5f;
                float v = (k >> 2) - 1.5f;
                cu += cur[o + k] * u;
                cv += cur[o + k] * v;
                bu += bg[o + k] * u;
                bv += bg[o + k] * v;
            }
            cu /= 20f;
            cv /= 20f;
            bu /= 20f;
            bv /= 20f;
            float ssc = 0;
            float ssb = 0;
            float sp = 0;
            for (int k = 0; k < N; k++) {
                float u = (k & 3) - 1.5f;
                float v = (k >> 2) - 1.5f;
                float dc = cur[o + k] - mc - cu * u - cv * v;
                float db = bg[o + k] - mb - bu * u - bv * v;
                ssc += dc * dc;
                ssb += db * db;
                sp += dc * db;
            }
            float rc = ssb > 1e-3f ? ssc - sp * sp / ssb : ssc;
            float rb = ssc > 1e-3f ? ssb - sp * sp / ssc : ssb;
            if (rc < 0) rc = 0;
            if (rb < 0) rb = 0;
            meanShift[i] = mc - mb;
            // Global-exposure vote: the cell's mean moved by more than ~25 % (or 6 levels in the dark).
            float dm = mc - mb;
            float lim = Math.max(6f, 0.25f * Math.max(mc, mb));
            if (dm > lim) gUp++;
            else if (dm < -lim) gDown++;
            resid[i] = Math.max(rc, rb) / N;
            rho2[i] = (ssc > 1e-3f && ssb > 1e-3f) ? (sp * sp) / (ssc * ssb) : 0f;
            valid[i] = hiC < 2 && hiB < 2 && loC < N / 2 && loB < N / 2;
            varMax[i] = Math.max(ssc, ssb) / N;
            int bin = (int) (Math.sqrt(Math.max(rc, rb) / N) * 4f);
            noiseHist[Math.min(63, bin)]++;
        }
        float med = 0;
        for (int b = 0, seen = 0; b < noiseHist.length; b++) {
            seen += noiseHist[b];
            if (seen * 2 >= cells) {
                med = (b + 0.5f) / 4f;
                break;
            }
        }
        med = Math.min(med, NOISE_CAP);
        noise = noise < 0 ? med : 0.5f * noise + 0.5f * med;
        float nf = Math.max(noise, med * 0.5f);
        float tex = Math.max(MIN_VAR, 6.25f * nf * nf);
        float thr = NOISE_K * nf + NOISE_C;
        thr *= thr;

        // 3. Decide per cell.
        int rawCount = 0;
        int textured = 0;
        int pos = 0;
        int neg = 0;
        for (int i = 0; i < cells; i++) {
            boolean isFlat = varMax[i] < tex;
            flat[i] = isFlat;
            boolean c = !isFlat && valid[i] && rho2[i] < RHO2_MAX && resid[i] > thr;
            raw[i] = c;
            if (!isFlat && valid[i]) textured++;
            if (c) {
                rawCount++;
                if (meanShift[i] > 3f) pos++;
                else if (meanShift[i] < -3f) neg++;
            }
        }
        float rawRatio = rawCount / (float) cells;
        // A shift of the mean level of most of the picture, in one direction, is an exposure or
        // lighting event whatever the pattern correlation says (a person never covers 60 % of the view).
        boolean globalShift = Math.max(gUp, gDown) >= GLOBAL_SHIFT_RATIO * cells
                && Math.min(gUp, gDown) <= 0.1f * Math.max(gUp, gDown);
        int signed = pos + neg;
        boolean coherent = signed >= rawCount / 2 && Math.max(pos, neg) >= ILLUM_COHERENCE * signed;
        if (globalShift || rawRatio >= ILLUM_ALWAYS || (coherent && (rawRatio >= ILLUM_RATIO || (rawRatio >= 0.06f && rawCount >= 0.6f * textured)))) {
            System.arraycopy(cur, 0, bg, 0, cur.length);
            suppress = ILLUM_SUPPRESS;
            lastRatio = 0f;
            lastIllumination = true;
            Arrays.fill(changedMask, false);
            return 0f;
        }

        // 4. Fill flat cells between changed cells, drop isolated cells.
        System.arraycopy(raw, 0, changedMask, 0, cells);
        int maxGapH = Math.max(3, gridW / 4);
        for (int y = 0; y < gridH; y++) {
            int last = -1;
            for (int x = 0; x < gridW; x++) {
                if (!raw[y * gridW + x]) continue;
                if (last >= 0 && x - last - 1 > 0 && x - last - 1 <= maxGapH) fillRow(y, last + 1, x);
                last = x;
            }
        }
        for (int x = 0; x < gridW; x++) {
            int last = -1;
            for (int y = 0; y < gridH; y++) {
                if (!raw[y * gridW + x]) continue;
                if (last >= 0 && y - last - 1 > 0 && y - last - 1 <= MAX_GAP_V) fillCol(x, last + 1, y);
                last = y;
            }
        }
        // Isolated cells are judged against a snapshot so that removal order does not matter.
        System.arraycopy(changedMask, 0, raw, 0, cells);
        int changed = 0;
        for (int y = 0; y < gridH; y++) {
            for (int x = 0; x < gridW; x++) {
                int i = y * gridW + x;
                if (!raw[i]) continue;
                if (hasNeighbour(raw, x, y)) changed++;
                else changedMask[i] = false;
            }
        }

        // 5. Update the background. A changed cell keeps its old pattern (so the empty scene is not
        // polluted by the passing object and a still-present object stays visible for a while);
        // after HOLD_ANALYSES in a row it becomes the new background. Others: EMA, alpha = 1/8.
        for (int i = 0; i < cells; i++) {
            int o = i * N;
            if (changedMask[i] && ++age[i] < HOLD_ANALYSES) continue;
            age[i] = 0;
            boolean reseed = changedMask[i];
            for (int k = o; k < o + N; k++) bg[k] = reseed ? cur[k] : bg[k] + (cur[k] - bg[k]) * 0.125f;
        }
        lastRatio = changed / (float) cells;
        return lastRatio;
    }

    private void fillRow(int y, int from, int to) {
        for (int x = from; x < to; x++) if (!flat[y * gridW + x]) return;
        for (int x = from; x < to; x++) changedMask[y * gridW + x] = true;
    }

    private void fillCol(int x, int from, int to) {
        for (int y = from; y < to; y++) if (!flat[y * gridW + x]) return;
        for (int y = from; y < to; y++) changedMask[y * gridW + x] = true;
    }

    private boolean hasNeighbour(boolean[] m, int x, int y) {
        for (int dy = -1; dy <= 1; dy++) {
            int yy = y + dy;
            if (yy < 0 || yy >= gridH) continue;
            for (int dx = -1; dx <= 1; dx++) {
                if (dx == 0 && dy == 0) continue;
                int xx = x + dx;
                if (xx < 0 || xx >= gridW) continue;
                if (m[yy * gridW + xx]) return true;
            }
        }
        return false;
    }
}
