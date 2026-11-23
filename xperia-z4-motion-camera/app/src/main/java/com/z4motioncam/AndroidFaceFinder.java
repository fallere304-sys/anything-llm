package com.z4motioncam;

import android.graphics.Bitmap;
import android.media.FaceDetector;
import android.util.Log;

import java.nio.ShortBuffer;

/** {@link HybridPersonDetector.FaceFinder} on the framework's android.media.FaceDetector (API 1). */
final class AndroidFaceFinder implements HybridPersonDetector.FaceFinder {
    private static final String TAG = "FaceFinder";
    private static final int MAX_FACES = 3;
    private Bitmap bitmap;
    private FaceDetector detector;
    private final FaceDetector.Face[] found = new FaceDetector.Face[MAX_FACES];
    private int failures;

    @Override
    public float bestConfidence(short[] rgb565, int width, int height) {
        if (failures >= 5) return 0f; // the platform detector is not usable on this device
        try {
            if (bitmap == null || bitmap.getWidth() != width || bitmap.getHeight() != height) {
                if (bitmap != null) bitmap.recycle();
                bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.RGB_565);
                detector = new FaceDetector(width, height, MAX_FACES);
            }
            bitmap.copyPixelsFromBuffer(ShortBuffer.wrap(rgb565, 0, width * height));
            int n = detector.findFaces(bitmap, found);
            float best = 0f;
            for (int i = 0; i < n; i++) {
                if (found[i] != null) best = Math.max(best, found[i].confidence());
            }
            failures = 0;
            return best;
        } catch (RuntimeException | UnsatisfiedLinkError e) {
            Log.w(TAG, "face detection failed", e);
            failures++;
            return 0f;
        }
    }

    boolean broken() {
        return failures >= 5;
    }

    @Override
    public void close() {
        if (bitmap != null) bitmap.recycle();
        bitmap = null;
        detector = null;
    }
}
