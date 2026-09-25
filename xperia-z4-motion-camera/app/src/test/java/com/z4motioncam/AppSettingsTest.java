package com.z4motioncam;

import static org.junit.Assert.assertArrayEquals;

import org.junit.Test;

public class AppSettingsTest {
    @Test
    public void parsesOnlyListedResolutions() {
        assertArrayEquals(new int[] {1920, 1080}, AppSettings.parseResolution("1920x1080"));
        assertArrayEquals(new int[] {320, 240}, AppSettings.parseResolution("320x240"));
        assertArrayEquals(new int[] {640, 480}, AppSettings.parseResolution("123x45"));
        assertArrayEquals(new int[] {640, 480}, AppSettings.parseResolution(null));
    }
}
