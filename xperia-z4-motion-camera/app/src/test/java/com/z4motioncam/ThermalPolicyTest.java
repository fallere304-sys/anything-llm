package com.z4motioncam;

import static com.z4motioncam.ThermalPolicy.Level.CRITICAL;
import static com.z4motioncam.ThermalPolicy.Level.HOT;
import static com.z4motioncam.ThermalPolicy.Level.NORMAL;
import static com.z4motioncam.ThermalPolicy.Level.WARM;
import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class ThermalPolicyTest {
    @Test
    public void heatsUpImmediately() {
        assertEquals(NORMAL, ThermalPolicy.next(NORMAL, 35f));
        assertEquals(WARM, ThermalPolicy.next(NORMAL, 39f));
        assertEquals(HOT, ThermalPolicy.next(NORMAL, 42.5f));
        assertEquals(CRITICAL, ThermalPolicy.next(WARM, 45f));
    }

    @Test
    public void coolsDownWithHysteresis() {
        assertEquals(CRITICAL, ThermalPolicy.next(CRITICAL, 43.5f));
        assertEquals(HOT, ThermalPolicy.next(CRITICAL, 42.9f));
        assertEquals(HOT, ThermalPolicy.next(HOT, 40.5f));
        assertEquals(WARM, ThermalPolicy.next(HOT, 39.9f));
        assertEquals(WARM, ThermalPolicy.next(WARM, 37.5f));
        assertEquals(NORMAL, ThermalPolicy.next(WARM, 36.9f));
        assertEquals(NORMAL, ThermalPolicy.next(CRITICAL, 30f));
    }

    @Test
    public void loadDropsAsLevelRises() {
        assertEquals(10, ThermalPolicy.recordFps(NORMAL, 10));
        assertEquals(8, ThermalPolicy.recordFps(WARM, 10));
        assertEquals(5, ThermalPolicy.recordFps(HOT, 15));
        assertEquals(5, ThermalPolicy.recordFps(HOT, 5));
        assertEquals(true, ThermalPolicy.liveIntervalMs(HOT) > ThermalPolicy.liveIntervalMs(NORMAL));
        assertEquals(true, ThermalPolicy.analyzeIntervalMs(HOT) > ThermalPolicy.analyzeIntervalMs(NORMAL));
    }
}
