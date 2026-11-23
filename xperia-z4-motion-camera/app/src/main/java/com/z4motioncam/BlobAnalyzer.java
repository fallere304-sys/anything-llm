package com.z4motioncam;

/**
 * Shape analysis of the motion grid: the largest connected group of changed cells (4-neighbour).
 * A person moving in the picture makes one compact group; sensor noise makes scattered cells and a
 * light switch or an exposure change makes (nearly) all of them change.
 */
final class BlobAnalyzer {
    /** Smallest group (share of the grid) that may be a person: a baby seen from a few metres is 5-15%. */
    static final float MIN_AREA = 0.04f;
    /** Above this the whole picture changed (lights, exposure, camera knocked): not a person. */
    static final float MAX_AREA = 0.65f;
    /** Cells filling this share of their bounding box: scattered noise fills little of it. */
    static final float MIN_FILL = 0.40f;
    static final float MIN_ASPECT = 0.25f;
    static final float MAX_ASPECT = 4.0f;
    /**
     * Silhouette candidate: a person far away or standing in a doorway is a thin, small group
     * (about 9% of the width and 40% of the height of the picture, under 4% of its area).
     */
    static final float SIL_MIN_AREA = 0.015f;
    static final float SIL_MIN_ASPECT = 0.12f;

    static final class Blob {
        final int cells;
        final int width;
        final int height;
        final int gridCells;
        /** Top-left cell of the bounding box. */
        final int minX;
        final int minY;

        Blob(int cells, int width, int height, int gridCells) {
            this(cells, width, height, gridCells, 0, 0);
        }

        Blob(int cells, int width, int height, int gridCells, int minX, int minY) {
            this.cells = cells;
            this.width = width;
            this.height = height;
            this.gridCells = gridCells;
            this.minX = minX;
            this.minY = minY;
        }

        float area() {
            return gridCells == 0 ? 0f : cells / (float) gridCells;
        }

        float fill() {
            return width * height == 0 ? 0f : cells / (float) (width * height);
        }

        float aspect() {
            return height == 0 ? 0f : width / (float) height;
        }

        boolean personLike() {
            float a = area();
            return a >= MIN_AREA && a <= MAX_AREA && fill() >= MIN_FILL && aspect() >= MIN_ASPECT && aspect() <= MAX_ASPECT;
        }

        /** Like {@link #personLike} but also accepts small and thin groups (people far away, in a doorway). */
        boolean silhouetteLike() {
            float a = area();
            return a >= SIL_MIN_AREA && a <= MAX_AREA && fill() >= MIN_FILL && aspect() >= SIL_MIN_ASPECT && aspect() <= MAX_ASPECT;
        }
    }

    private BlobAnalyzer() {}

    /** Largest connected group, or an empty blob for a null / malformed mask. */
    static Blob largest(boolean[] mask, int gw, int gh) {
        if (mask == null || gw <= 0 || gh <= 0 || mask.length < gw * gh) return new Blob(0, 0, 0, Math.max(0, gw * gh));
        boolean[] seen = new boolean[gw * gh];
        int[] stack = new int[gw * gh];
        int bestCells = 0;
        int bestW = 0;
        int bestH = 0;
        int bestX = 0;
        int bestY = 0;
        for (int start = 0; start < gw * gh; start++) {
            if (!mask[start] || seen[start]) continue;
            int sp = 0;
            stack[sp++] = start;
            seen[start] = true;
            int cells = 0;
            int minX = gw;
            int maxX = -1;
            int minY = gh;
            int maxY = -1;
            while (sp > 0) {
                int i = stack[--sp];
                int x = i % gw;
                int y = i / gw;
                cells++;
                if (x < minX) minX = x;
                if (x > maxX) maxX = x;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
                if (x > 0 && mask[i - 1] && !seen[i - 1]) { seen[i - 1] = true; stack[sp++] = i - 1; }
                if (x < gw - 1 && mask[i + 1] && !seen[i + 1]) { seen[i + 1] = true; stack[sp++] = i + 1; }
                if (y > 0 && mask[i - gw] && !seen[i - gw]) { seen[i - gw] = true; stack[sp++] = i - gw; }
                if (y < gh - 1 && mask[i + gw] && !seen[i + gw]) { seen[i + gw] = true; stack[sp++] = i + gw; }
            }
            if (cells > bestCells) {
                bestCells = cells;
                bestW = maxX - minX + 1;
                bestH = maxY - minY + 1;
                bestX = minX;
                bestY = minY;
            }
        }
        return new Blob(bestCells, bestW, bestH, gw * gh, bestX, bestY);
    }
}
