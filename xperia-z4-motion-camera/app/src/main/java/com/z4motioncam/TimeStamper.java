package com.z4motioncam;

import android.media.MediaCodecInfo;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Burns "yyyy/MM/dd HH:mm:ss" into the bottom-left corner of frames bound for the encoder: white
 * text on a black box, written straight into the NV12 / I420 buffer after the NV21 conversion.
 *
 * <p>Cheap on purpose: the box is a few thousand pixels (about 1 % of a 640x480 frame), the text
 * bitmap is rebuilt only when the second changes, and the buffer positions are computed once. No
 * Canvas, no GPU. The recording carries a rotation hint instead of rotated pixels, so the text is
 * drawn rotated the other way and reads upright in the player.
 *
 * <p>Not thread-safe: used on the camera thread only.
 */
@SuppressWarnings("deprecation") // COLOR_FormatYUV420Planar: the documented ByteBuffer input on API 21.
final class TimeStamper {
    static final String PATTERN = "yyyy/MM/dd HH:mm:ss";
    private static final byte TEXT_Y = (byte) 235;
    private static final byte BOX_Y = (byte) 16;
    private static final byte NEUTRAL_UV = (byte) 128;

    /** 5x7 glyphs, one row per int (bit 4 = leftmost column). */
    private static final String GLYPH_CHARS = "0123456789/: ";
    private static final int[][] GLYPHS = {
            {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}, // 0
            {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E}, // 1
            {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}, // 2
            {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E}, // 3
            {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}, // 4
            {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E}, // 5
            {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}, // 6
            {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08}, // 7
            {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}, // 8
            {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C}, // 9
            {0x01, 0x01, 0x02, 0x04, 0x08, 0x10, 0x10}, // /
            {0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00}, // :
            {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}, // space
    };

    private final int scale;
    private final int boxW;
    private final int boxH;
    private final int pad;
    /** Buffer index of the luma sample behind each box pixel (row-major in display orientation). */
    private final int[] lumaIndex;
    /** Buffer indices of the chroma samples under the box (set to grey so the box has no tint). */
    private final int[] chromaIndex;
    private final byte[] pixels;
    private final SimpleDateFormat format = new SimpleDateFormat(PATTERN, Locale.US);
    private long shownSecond = Long.MIN_VALUE;

    /**
     * @param width      frame width as stored (sensor orientation)
     * @param height     frame height as stored
     * @param stride     row stride of the encoder buffer
     * @param sliceHeight rows per plane of the encoder buffer
     * @param colorFormat NV12 (semi-planar) or I420 (planar)
     * @param rotation   the recording's rotation hint (0 / 90 / 180 / 270, clockwise)
     */
    TimeStamper(int width, int height, int stride, int sliceHeight, int colorFormat, int rotation) {
        boolean swap = rotation == 90 || rotation == 270;
        int dispW = swap ? height : width;
        int dispH = swap ? width : height;
        scale = Math.max(1, Math.min(dispW, dispH) / 240);
        pad = 2 * scale;
        int chars = PATTERN.length();
        boxW = Math.min(dispW, chars * 6 * scale - scale + 2 * pad);
        boxH = Math.min(dispH, 7 * scale + 2 * pad);
        int left = Math.min(2 * scale, dispW - boxW);
        int top = Math.max(0, dispH - boxH - 2 * scale);

        lumaIndex = new int[boxW * boxH];
        chromaIndex = new int[boxW * boxH * 2];
        pixels = new byte[boxW * boxH];
        boolean planar = colorFormat == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar;
        int chromaStart = stride * sliceHeight;
        int vStart = chromaStart + (stride / 2) * (sliceHeight / 2);
        int i = 0;
        for (int y = 0; y < boxH; y++) {
            for (int x = 0; x < boxW; x++, i++) {
                int dx = left + x;
                int dy = top + y;
                int sx;
                int sy;
                switch (rotation) {
                    case 90: sx = dy; sy = height - 1 - dx; break;
                    case 180: sx = width - 1 - dx; sy = height - 1 - dy; break;
                    case 270: sx = width - 1 - dy; sy = dx; break;
                    default: sx = dx; sy = dy; break;
                }
                lumaIndex[i] = sy * stride + sx;
                if (planar) {
                    int c = (sy / 2) * (stride / 2) + sx / 2;
                    chromaIndex[2 * i] = chromaStart + c;
                    chromaIndex[2 * i + 1] = vStart + c;
                } else {
                    int c = chromaStart + (sy / 2) * stride + (sx / 2) * 2;
                    chromaIndex[2 * i] = c;
                    chromaIndex[2 * i + 1] = c + 1;
                }
            }
        }
    }

    /** Draws the time {@code wallMs} (device time zone) into {@code yuv}. */
    void stamp(byte[] yuv, long wallMs) {
        long second = wallMs / 1000L; // Math.floorDiv needs API 24; the clock is never negative here
        if (second != shownSecond) {
            shownSecond = second;
            render(format.format(new Date(second * 1000L)));
        }
        for (int i = 0; i < lumaIndex.length; i++) yuv[lumaIndex[i]] = pixels[i];
        for (int c : chromaIndex) yuv[c] = NEUTRAL_UV;
    }

    private void render(String text) {
        java.util.Arrays.fill(pixels, BOX_Y);
        for (int k = 0; k < text.length(); k++) {
            int g = GLYPH_CHARS.indexOf(text.charAt(k));
            if (g < 0) continue;
            int[] rows = GLYPHS[g];
            int x0 = pad + k * 6 * scale;
            for (int r = 0; r < 7; r++) {
                for (int col = 0; col < 5; col++) {
                    if ((rows[r] & (0x10 >> col)) == 0) continue;
                    for (int yy = 0; yy < scale; yy++) {
                        int py = pad + r * scale + yy;
                        if (py >= boxH) continue;
                        for (int xx = 0; xx < scale; xx++) {
                            int px = x0 + col * scale + xx;
                            if (px < boxW) pixels[py * boxW + px] = TEXT_Y;
                        }
                    }
                }
            }
        }
    }

    int boxWidth() {
        return boxW;
    }

    int boxHeight() {
        return boxH;
    }
}
