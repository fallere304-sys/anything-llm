package com.z4motioncam;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertNull;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import org.junit.Test;

public class CameraPipelineTest {
    // Typical Qualcomm list (milli-fps).
    private static final List<int[]> RANGES = Arrays.asList(
            new int[] {7500, 30000}, new int[] {8000, 30000}, new int[] {15000, 15000},
            new int[] {30000, 30000}, new int[] {7500, 7500}, new int[] {7500, 15000});

    @Test
    public void picksSlowestRangeThatReachesTheTarget() {
        assertArrayEquals(new int[] {7500, 7500}, CameraPipeline.chooseFpsRange(RANGES, 5000));
        assertArrayEquals(new int[] {7500, 15000}, CameraPipeline.chooseFpsRange(RANGES, 10000));
        assertArrayEquals(new int[] {7500, 30000}, CameraPipeline.chooseFpsRange(RANGES, 24000));
        assertArrayEquals(new int[] {7500, 7500}, CameraPipeline.chooseFpsRange(RANGES, 1000));
        // Nothing fast enough: the fastest one.
        assertArrayEquals(new int[] {15000, 15000},
                CameraPipeline.chooseFpsRange(Collections.singletonList(new int[] {15000, 15000}), 30000));
        assertNull(CameraPipeline.chooseFpsRange(Collections.<int[]>emptyList(), 10000));
    }

    @Test
    public void downscalesNv21ByTwo() {
        int w = 4, h = 4;
        byte[] src = new byte[w * h * 3 / 2];
        for (int i = 0; i < w * h; i++) src[i] = (byte) i;           // Y 0..15
        byte[] uv = {100, 101, 102, 103, 104, 105, 106, 107};         // 2 rows of V,U pairs
        System.arraycopy(uv, 0, src, w * h, uv.length);
        byte[] dst = new byte[2 * 2 * 3 / 2];
        CameraPipeline.downscale2x(src, w, h, dst);
        // Y: rows 0 and 2, columns 0 and 2; UV: first row, first pair.
        assertArrayEquals(new byte[] {0, 2, 8, 10, 100, 101}, dst);
    }
}
