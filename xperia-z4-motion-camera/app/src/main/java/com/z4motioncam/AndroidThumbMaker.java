package com.z4motioncam;

import android.graphics.Bitmap;
import android.graphics.Matrix;
import android.media.MediaMetadataRetriever;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;

/**
 * Thumbnail of an MP4 with the platform's own decoder: one frame from near the start (about 1 s in,
 * where the motion that triggered the recording usually is), scaled to 160 px wide and saved as JPEG.
 * About 5 KB per recording, and it runs once per recording.
 */
final class AndroidThumbMaker implements RecordingStore.ThumbMaker {
    static final int WIDTH = 160;
    private static final int QUALITY = 70;

    @Override
    public boolean make(File video, File out) {
        MediaMetadataRetriever r = new MediaMetadataRetriever();
        Bitmap frame = null;
        Bitmap scaled = null;
        try {
            r.setDataSource(video.getAbsolutePath());
            long durationMs = parse(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION), 0);
            long atUs = Math.min(1_000_000L, durationMs * 1000L / 2);
            frame = r.getFrameAtTime(atUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
            if (frame == null) frame = r.getFrameAtTime(0);
            if (frame == null) return false;
            int rotation = (int) parse(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION), 0);
            int vw = (int) parse(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH), 0);
            int vh = (int) parse(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT), 0);
            Bitmap upright = frame;
            // Some versions hand back the stored (unrotated) picture: turn it when it is still the stored shape.
            if ((rotation == 90 || rotation == 270) && vw > 0 && vh > 0
                    && frame.getWidth() == vw && frame.getHeight() == vh && vw != vh) {
                Matrix m = new Matrix();
                m.postRotate(rotation);
                upright = Bitmap.createBitmap(frame, 0, 0, frame.getWidth(), frame.getHeight(), m, true);
            }
            int h = Math.max(1, upright.getHeight() * WIDTH / Math.max(1, upright.getWidth()));
            scaled = Bitmap.createScaledBitmap(upright, WIDTH, h, true);
            if (upright != frame) upright.recycle();
            FileOutputStream fos = new FileOutputStream(out);
            try {
                return scaled.compress(Bitmap.CompressFormat.JPEG, QUALITY, fos);
            } finally {
                fos.close();
            }
        } catch (IOException | RuntimeException e) {
            return false; // unreadable / unsupported file: the page shows a blank thumbnail
        } finally {
            if (scaled != null) scaled.recycle();
            if (frame != null) frame.recycle();
            try {
                r.release();
            } catch (RuntimeException ignored) {
                // already released
            }
        }
    }

    private static long parse(String s, long def) {
        try {
            return s == null ? def : Long.parseLong(s.trim());
        } catch (NumberFormatException e) {
            return def;
        }
    }
}
