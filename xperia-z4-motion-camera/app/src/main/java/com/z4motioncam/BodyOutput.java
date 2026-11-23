package com.z4motioncam;

/** What the SSD model's post-processing op returns: up to {@link #MAX} detections, best first. */
final class BodyOutput {
    static final int MAX = 10;
    /** (ymin, xmin, ymax, xmax) per detection, 0..1 of the input picture. */
    final float[] boxes = new float[MAX * 4];
    /** Class index per detection (0 = person: the label map's "???" background slot is not counted). */
    final float[] classes = new float[MAX];
    final float[] scores = new float[MAX];
    int count;
}
