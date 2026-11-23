package com.z4motioncam;

import java.util.Arrays;

/**
 * Motion detector that looks at <em>structure</em>, not at brightness, working on the luma plane
 * of NV21 frames. A light switch, a cloud, a sunny patch or an auto-exposure step changes how
 * bright things are, but not their outlines; a person or a hand moving changes the outlines.
 *
 * <p>Every cell of a coarse grid (about 32 columns) is sampled on 6x6 points spread over 1.5 times
 * the cell's size (neighbouring patches overlap, so an outline that runs exactly along a cell border
 * is still seen); each point is the mean of two neighbouring pixels, about 55k reads per analysis. From the 36 values the cell's mean and its
 * best-fit plane (a brightness ramp across the cell) are removed, leaving only the local texture /
 * edge pattern. That residual is compared with a slowly adapting background residual by
 * <em>regression</em>: the current pattern is explained as "background pattern times a gain" and
 * only what the gain cannot explain counts. So the comparison is blind to:
 * <ul>
 *   <li>offset changes (lights, day patches): the mean is removed;
 *   <li>gain / gamma-like changes, locally too (a spot light, a shadow's strength): the gain is fitted per cell;
 *   <li>smooth ramps (the soft edge of a sunny patch): the plane is removed;
 *   <li>sensor noise: the noise level is measured from the frame itself (median over cells) and the
 *       limit follows it, so a noisy dark room needs a clearer change than a quiet bright one;
 *   <li>clipped cells (light turned on until it saturates).
 * </ul>
 * A change of the whole picture (lights, exposure) is recognised from the shift of the cell means
 * and the background is simply re-based; the next two analyses stay quiet while the exposure settles.
 *
 * <p>Cells made only of a moving object's outline leave a hollow shape on a flat wall, so enclosed
 * holes are filled afterwards (a cell between changed cells in both its row and its column).
 */
final class MotionDetector {
    /** Samples per cell side. */
    private static final int P = 6;
    private static final int N = P * P;
    /** Sum of squared centred coordinates over the 36 samples, per axis. */
    private static final float SUM_C2 = 105f;
    /** Gain-fitted pattern correlation at or above this means "the same outline". */
    static final float RHO_MAX = 0.80f;
    /** Never trust a pattern change smaller than this (pixel levels, residual std). */
    static final float MIN_NOISE_STD = 3f;
    static final float NOISE_FACTOR = 2.5f;
    /** Largest local contrast change (current / background pattern amplitude) explained as lighting. */
    static final float MAX_GAIN = 3f;
    /** Whole-picture brightness shift (median cell ratio) that counts as a lighting event. */
    private static final float GLOBAL_SHIFT = 0.12f;
    private static final float GLOBAL_SHIFT_WITH_MANY_CELLS = 0.05f;
    private static final float GLOBAL_UNIFORM_SHARE = 0.5f;
    private static final float MANY_CELLS = 0.5f;
    /** Analyses to keep quiet after a lighting event (the auto exposure keeps settling). */
    private static final int SETTLE_ANALYSES = 2;
    /** Holes up to this many cells wide are filled. */
    private static final int MAX_GAP = 7;
    /** Consecutive analyses over the threshold required to trigger (filters sensor noise). */
    private static final int REQUIRED_HITS = 2;
    private static final float[] OFF = new float[N * 2];

    static {
        for (int j = 0; j < P; j++) {
            for (int i = 0; i < P; i++) {
                OFF[j * P + i] = i - 2.5f;
                OFF[N + j * P + i] = j - 2.5f;
            }
        }
    }

    private final int gridW;
    private final int gridH;
    private final int cells;
    private final float[] curR;
    private final float[] bgR;
    private final float[] curMean;
    private final float[] bgMean;
    private final float[] fitC;
    private final float[] fitB;
    private final float[] rho;
    private final float[] ratios;
    private final float[] tmp = new float[N];
    private final int[] noiseHist = new int[256];
    private final boolean[] flagged;
    private final boolean[] rowSpan;
    private final boolean[] colSpan;
    /** Cells that changed in the last analysis (for the shape check of the person mode). */
    private final boolean[] changedMask;
    private boolean initialized;
    private int hits;
    private int settle;
    private float lastRatio;
    private boolean lastGlobal;

    /** @param step ignored: kept for source compatibility, the sampling now follows the cell size */
    MotionDetector(int gridW, int gridH, int step) {
        this.gridW = gridW;
        this.gridH = gridH;
        this.cells = gridW * gridH;
        curR = new float[cells * N];
        bgR = new float[cells * N];
        curMean = new float[cells];
        bgMean = new float[cells];
        fitC = new float[cells];
        fitB = new float[cells];
        rho = new float[cells];
        ratios = new float[cells];
        flagged = new boolean[cells];
        rowSpan = new boolean[cells];
        colSpan = new boolean[cells];
        changedMask = new boolean[cells];
    }

    /** Grid of roughly 32 columns with the frame's aspect ratio. */
    static MotionDetector forFrame(int width, int height) {
        int gw = 32;
        int gh = Math.max(1, Math.round(gw * (float) height / width));
        return new MotionDetector(gw, gh, 0);
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

    /** True when the last analysis was discarded as a whole-picture brightness event. */
    boolean lastWasLightingEvent() {
        return lastGlobal;
    }

    /** Changed cells of the last analysis; reused between calls, read it right away. */
    boolean[] changedMask() {
        return changedMask;
    }

    void reset() {
        initialized = false;
        hits = 0;
        settle = 0;
        lastRatio = 0f;
        lastGlobal = false;
        Arrays.fill(changedMask, false);
    }

    /**
     * Analyses one frame and returns true when motion is detected.
     *
     * @param areaThreshold fraction of cells (0..1) that must change to count as motion
     */
    boolean update(byte[] nv21, int width, int height, float areaThreshold) {
        float ratio = analyze(nv21, width, height);
        if (ratio >= areaThreshold && ratio > 0f) {
            hits++;
        } else {
            hits = 0;
        }
        return hits >= REQUIRED_HITS;
    }

    /** Returns the fraction of grid cells whose outline changed against the background (0..1). */
    float analyze(byte[] nv21, int width, int height) {
        sample(nv21, width, height);
        if (!initialized) {
            rebase();
            initialized = true;
            lastRatio = 0f;
            lastGlobal = false;
            Arrays.fill(changedMask, false);
            return 0f;
        }

        // A lighting event: (nearly) the whole picture got brighter or darker together.
        float med = medianBrightnessRatio();
        float shift = Math.abs(med - 1f);
        int uniform = 0;
        for (int c = 0; c < cells; c++) {
            float r = ratios[c];
            if (r >= med * 0.88f && r <= med * 1.12f) uniform++;
        }
        boolean uniformShift = shift > GLOBAL_SHIFT && uniform >= cells * GLOBAL_UNIFORM_SHARE;
        compare();
        float noiseStd = noiseStd();
        float limit = Math.max(MIN_NOISE_STD, NOISE_FACTOR * noiseStd);
        int flaggedCount = 0;
        for (int c = 0; c < cells; c++) {
            boolean f = Math.max(fitC[c], fitB[c]) > limit && rho[c] < RHO_MAX;
            if (f && fitB[c] > fitC[c] && curMean[c] > 250f && fitC[c] <= limit) f = false; // clipped to white
            if (f && fitB[c] > fitC[c] && curMean[c] < 4f && fitC[c] <= limit) f = false; // crushed to black
            flagged[c] = f;
            if (f) flaggedCount++;
        }
        boolean event = uniformShift || (flaggedCount > cells * MANY_CELLS && shift > GLOBAL_SHIFT_WITH_MANY_CELLS);
        if (event || settle > 0) {
            if (event) settle = SETTLE_ANALYSES;
            else settle--;
            rebase();
            hits = 0;
            lastRatio = 0f;
            lastGlobal = event;
            Arrays.fill(changedMask, false);
            return 0f;
        }
        lastGlobal = false;
        fillHoles();
        int changed = 0;
        for (int c = 0; c < cells; c++) {
            if (changedMask[c]) changed++;
        }
        lastRatio = changed / (float) cells;
        // Exponential moving average of the background pattern, alpha = 1/8.
        for (int k = 0, n = cells * N; k < n; k++) bgR[k] += (curR[k] - bgR[k]) * 0.125f;
        for (int c = 0; c < cells; c++) bgMean[c] += (curMean[c] - bgMean[c]) * 0.125f;
        return lastRatio;
    }

    private void rebase() {
        System.arraycopy(curR, 0, bgR, 0, cells * N);
        System.arraycopy(curMean, 0, bgMean, 0, cells);
    }

    /** Reads the 6x6 sample points of every cell and stores mean and plane-free residual. */
    private void sample(byte[] nv21, int width, int height) {
        int cellW = width / gridW;
        int cellH = height / gridH;
        float[] t = tmp;
        for (int gy = 0; gy < gridH; gy++) {
            for (int gx = 0; gx < gridW; gx++) {
                int x0 = gx * cellW;
                int y0 = gy * cellH;
                float sum = 0f;
                float sumI = 0f;
                float sumJ = 0f;
                for (int j = 0; j < P; j++) {
                    int row = clamp(y0 + cellH / 2 + ((2 * j - 5) * cellH) / 8, height - 1) * width;
                    for (int i = 0; i < P; i++) {
                        int x = clamp(x0 + cellW / 2 + ((2 * i - 5) * cellW) / 8, width - 2);
                        int x2 = x + 1;
                        float v = ((nv21[row + x] & 0xFF) + (nv21[row + x2] & 0xFF)) * 0.5f;
                        int k = j * P + i;
                        t[k] = v;
                        sum += v;
                        sumI += v * OFF[k];
                        sumJ += v * OFF[N + k];
                    }
                }
                float mean = sum / N;
                float a = sumI / SUM_C2;
                float b = sumJ / SUM_C2;
                int c = gy * gridW + gx;
                int base = c * N;
                for (int k = 0; k < N; k++) curR[base + k] = t[k] - mean - a * OFF[k] - b * OFF[N + k];
                curMean[c] = mean;
            }
        }
    }

    private static int clamp(int v, int max) {
        return v < 0 ? 0 : Math.min(v, max);
    }

    /** Median of (current mean + 8) / (background mean + 8) over all cells; fills {@link #ratios}. */
    private float medianBrightnessRatio() {
        for (int c = 0; c < cells; c++) ratios[c] = (curMean[c] + 8f) / (bgMean[c] + 8f);
        float[] s = Arrays.copyOf(ratios, cells);
        Arrays.sort(s);
        return s[cells / 2];
    }

    /** Per cell: residual energy of each pattern after explaining it by the other one times a gain. */
    private void compare() {
        for (int c = 0; c < cells; c++) {
            int base = c * N;
            float ec = 0f;
            float eb = 0f;
            float x = 0f;
            for (int k = 0; k < N; k++) {
                float a = curR[base + k];
                float b = bgR[base + k];
                ec += a * a;
                eb += b * b;
                x += a * b;
            }
            float fc = ec;
            float fb = eb;
            float rh = 0f;
            // A gain beyond a few times is no lighting change but an outline that (dis)appeared:
            // do not let the fit explain it away (a new object that stays one frame, the
            // background having absorbed 1/8 of it, would otherwise look like "same pattern, more gain").
            float gain = eb > 1e-3f ? x / eb : 0f;
            boolean gainOk = gain >= 1f / MAX_GAIN && gain <= MAX_GAIN;
            if (eb > 1e-3f && gainOk) fc = Math.max(0f, ec - x * x / eb);
            if (ec > 1e-3f && gainOk) fb = Math.max(0f, eb - x * x / ec);
            if (ec > 1e-3f && eb > 1e-3f && gainOk) rh = x / (float) Math.sqrt((double) ec * eb);
            fitC[c] = (float) Math.sqrt(fc / (N - 3));
            fitB[c] = (float) Math.sqrt(fb / (N - 3));
            rho[c] = rh;
        }
    }

    /** Median of the cells' fitted residual: what "nothing moved" looks like at this noise level. */
    private float noiseStd() {
        Arrays.fill(noiseHist, 0);
        for (int c = 0; c < cells; c++) noiseHist[Math.min(255, (int) (fitC[c] * 4f))]++;
        for (int b = 0, seen = 0; b < noiseHist.length; b++) {
            seen += noiseHist[b];
            if (seen * 2 >= cells) return b / 4f;
        }
        return 0f;
    }

    /** changedMask = flagged cells plus cells enclosed by flagged cells horizontally and vertically. */
    private void fillHoles() {
        Arrays.fill(rowSpan, false);
        Arrays.fill(colSpan, false);
        for (int y = 0; y < gridH; y++) {
            int last = -1;
            for (int x = 0; x < gridW; x++) {
                if (!flagged[y * gridW + x]) continue;
                if (last >= 0 && x - last - 1 <= MAX_GAP) for (int k = last; k <= x; k++) rowSpan[y * gridW + k] = true;
                last = x;
            }
        }
        for (int x = 0; x < gridW; x++) {
            int last = -1;
            for (int y = 0; y < gridH; y++) {
                if (!flagged[y * gridW + x]) continue;
                if (last >= 0 && y - last - 1 <= MAX_GAP) for (int k = last; k <= y; k++) colSpan[k * gridW + x] = true;
                last = y;
            }
        }
        for (int c = 0; c < cells; c++) changedMask[c] = flagged[c] || (rowSpan[c] && colSpan[c]);
    }
}
