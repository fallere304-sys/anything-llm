package com.z4motioncam;

import static org.junit.Assert.assertArrayEquals;

import org.junit.Test;

public class VideoRecorderConvertTest {
    // 4x2 frame: 8 luma bytes, then 2 VU pairs.
    private static final byte[] NV21 = {1, 2, 3, 4, 5, 6, 7, 8, /* V U */ 20, 10, 21, 11};

    @Test
    public void nv21ToNv12SwapsChromaPairs() {
        byte[] out = new byte[NV21.length];
        VideoRecorder.convert(NV21, out, 4, 2, 21 /* COLOR_FormatYUV420SemiPlanar */);
        assertArrayEquals(new byte[] {1, 2, 3, 4, 5, 6, 7, 8, 10, 20, 11, 21}, out);
    }

    @Test
    public void nv21ToI420SplitsPlanes() {
        byte[] out = new byte[NV21.length];
        VideoRecorder.convert(NV21, out, 4, 2, 19 /* COLOR_FormatYUV420Planar */);
        assertArrayEquals(new byte[] {1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 20, 21}, out);
    }
}
