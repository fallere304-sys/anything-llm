package com.z4motioncam;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Restarts monitoring after a reboot (power cut, thermal shutdown, OS update). */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        if (!AppSettings.load(context).autostart) return;
        // The service first: newer Android versions may refuse to open a screen from here, and
        // monitoring must not depend on it (the service brings the screen up itself later).
        context.startService(new Intent(context, CameraService.class));
        try {
            context.startActivity(new Intent(context, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra(MainActivity.EXTRA_START_DARK, true));
        } catch (RuntimeException ignored) {
            // refused: the service retries
        }
    }
}
