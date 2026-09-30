package com.z4motioncam;

/**
 * Whether one of the app's own screens is on display (started and not stopped). The service uses
 * it to notice that the app was sent to the back or the screen went off. Main thread only.
 */
final class AppUi {
    private static int started;

    private AppUi() {}

    static void onStart() {
        started++;
    }

    static void onStop() {
        if (started > 0) started--;
    }

    static boolean visible() {
        return started > 0;
    }
}
