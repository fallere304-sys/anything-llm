package com.z4motioncam;

/**
 * Maps the battery temperature to a load level. The battery sensor is the only temperature that
 * is readable without root on Android 5-7, and it is what the charger IC itself reacts to.
 */
final class ThermalPolicy {
    enum Level { NORMAL, WARM, HOT, CRITICAL }

    static final float WARM_C = 39f;
    static final float HOT_C = 42f;
    static final float CRITICAL_C = 45f;
    /** A level is left only after cooling this far below its entry temperature. */
    static final float HYSTERESIS_C = 2f;

    private ThermalPolicy() {}

    static Level next(Level prev, float tempC) {
        Level up = levelFor(tempC, 0f);
        if (up.ordinal() >= prev.ordinal()) return up;
        // Cooling down: only step down once below the threshold minus the hysteresis.
        Level down = levelFor(tempC, HYSTERESIS_C);
        return down.ordinal() < prev.ordinal() ? down : prev;
    }

    private static Level levelFor(float t, float margin) {
        if (t >= CRITICAL_C - margin) return Level.CRITICAL;
        if (t >= HOT_C - margin) return Level.HOT;
        if (t >= WARM_C - margin) return Level.WARM;
        return Level.NORMAL;
    }

    /** Minimum interval between motion analyses. */
    static long analyzeIntervalMs(Level l) {
        switch (l) {
            case NORMAL: return 333;
            case WARM: return 500;
            default: return 1000;
        }
    }

    /** Minimum interval between JPEG frames for live viewers. */
    static long liveIntervalMs(Level l) {
        switch (l) {
            case NORMAL: return 200;
            case WARM: return 500;
            default: return 1000;
        }
    }

    /** Recording frame rate cap. */
    static int recordFps(Level l, int configured) {
        switch (l) {
            case NORMAL: return configured;
            case WARM: return Math.min(configured, 8);
            default: return Math.min(configured, 5);
        }
    }
}
