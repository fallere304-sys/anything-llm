package com.z4motioncam;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.media.MediaCodecInfo;

import java.util.Arrays;
import java.util.TimeZone;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

public class TimeStamperTest {
    private static final int NV12 = MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar;
    private static final int I420 = MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar;
    /** 2026/09/28 03:12:45 UTC. */
    private static final long T = 1790565165000L;
    private TimeZone saved;

    @Before
    public void utc() {
        saved = TimeZone.getDefault();
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
    }

    @After
    public void restore() {
        TimeZone.setDefault(saved);
    }

    private static byte[] frame(int stride, int sliceHeight) {
        byte[] b = new byte[stride * sliceHeight * 3 / 2];
        Arrays.fill(b, 0, stride * sliceHeight, (byte) 100);
        Arrays.fill(b, stride * sliceHeight, b.length, (byte) 50);
        return b;
    }

    @Test
    public void readsUprightInThePlayerForEveryRotation() {
        int w = 640;
        int h = 480;
        for (int rot : new int[] {0, 90, 180, 270}) {
            byte[] stored = frame(w, h);
            new TimeStamper(w, h, w, h, NV12, rot).stamp(stored, T);
            // What the player shows: the stored picture turned clockwise by the rotation hint.
            boolean swap = rot == 90 || rot == 270;
            int dw = swap ? h : w;
            int dh = swap ? w : h;
            byte[] shown = new byte[dw * dh];
            for (int dy = 0; dy < dh; dy++) {
                for (int dx = 0; dx < dw; dx++) {
                    int sx;
                    int sy;
                    switch (rot) {
                        case 90: sx = dy; sy = h - 1 - dx; break;
                        case 180: sx = w - 1 - dx; sy = h - 1 - dy; break;
                        case 270: sx = w - 1 - dy; sy = dx; break;
                        default: sx = dx; sy = dy; break;
                    }
                    shown[dy * dw + dx] = stored[sy * w + sx];
                }
            }
            byte[] expected = frame(dw, dh);
            new TimeStamper(dw, dh, dw, dh, NV12, 0).stamp(expected, T);
            assertTrue("rotation " + rot, Arrays.equals(Arrays.copyOf(expected, dw * dh), shown));
        }
    }

    @Test
    public void drawsTheTimeBottomLeftAndTouchesNothingElse() {
        int w = 640;
        int h = 480;
        byte[] f = frame(w, h);
        TimeStamper s = new TimeStamper(w, h, w, h, NV12, 0);
        s.stamp(f, T);
        int scale = 2; // 480 lines -> 2x glyphs
        int bw = s.boxWidth();
        int bh = s.boxHeight();
        assertEquals(19 * 6 * scale - scale + 4 * scale, bw);
        assertEquals(7 * scale + 4 * scale, bh);
        int left = 2 * scale;
        int top = h - bh - 2 * scale;
        int changedOutside = 0;
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                boolean in = x >= left && x < left + bw && y >= top && y < top + bh;
                if (!in && f[y * w + x] != 100) changedOutside++;
            }
        }
        assertEquals(0, changedOutside);
        // First glyph "2": top row 0x0E -> columns 1..3 lit, column 0 dark.
        int gx = left + 2 * scale;
        int gy = top + 2 * scale;
        assertEquals((byte) 16, f[gy * w + gx]);
        assertEquals((byte) 235, f[gy * w + gx + scale]);
        assertEquals((byte) 235, f[gy * w + gx + 3 * scale]);
        assertEquals((byte) 16, f[gy * w + gx + 4 * scale]);
        // Chroma under the box is neutral grey, elsewhere untouched.
        int cy = (top + bh / 2) / 2;
        int cx = (left + bw / 2) / 2;
        assertEquals((byte) 128, f[w * h + cy * w + cx * 2]);
        assertEquals((byte) 128, f[w * h + cy * w + cx * 2 + 1]);
        assertEquals((byte) 50, f[w * h]);
    }

    @Test
    public void respectsPaddedI420BuffersAndSecondChanges() {
        int w = 1920;
        int h = 1080;
        int stride = 1920;
        int slice = 1088;
        byte[] a = frame(stride, slice);
        byte[] b = frame(stride, slice);
        TimeStamper s = new TimeStamper(w, h, stride, slice, I420, 90);
        s.stamp(a, T);
        s.stamp(b, T + 1000);
        assertTrue("next second draws different digits", !Arrays.equals(a, b));
        // Padding rows 1080..1087 of the luma plane are left alone.
        for (int y = h; y < slice; y++) {
            for (int x = 0; x < w; x++) assertEquals((byte) 100, a[y * stride + x]);
        }
        byte[] c = frame(stride, slice);
        s.stamp(c, T + 999);
        byte[] d = frame(stride, slice);
        s.stamp(d, T);
        assertTrue("same second, same picture", Arrays.equals(c, d));
    }

    @Test
    public void smallFramesStillFit() {
        byte[] f = frame(320, 240);
        TimeStamper s = new TimeStamper(320, 240, 320, 240, NV12, 90);
        s.stamp(f, T);
        assertTrue(s.boxWidth() <= 240);
    }

    @Test
    public void costIsSmall() {
        int w = 640;
        int h = 480;
        byte[] f = frame(w, h);
        TimeStamper s = new TimeStamper(w, h, w, h, NV12, 0);
        long start = System.nanoTime();
        for (int i = 0; i < 1000; i++) s.stamp(f, T + i * 100L);
        long perFrameUs = (System.nanoTime() - start) / 1000 / 1000;
        // Generous bound for slow CI machines; typically a few microseconds.
        assertTrue("per frame " + perFrameUs + " us", perFrameUs < 2000);
    }
}
