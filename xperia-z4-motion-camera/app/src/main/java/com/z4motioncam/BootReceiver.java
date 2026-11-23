package com.z4motioncam;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.preference.PreferenceManager;

/** Restarts monitoring after a reboot (power cut, thermal shutdown, OS update). */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        boolean replaced = Intent.ACTION_MY_PACKAGE_REPLACED.equals(action);
        if (!replaced && !Intent.ACTION_BOOT_COMPLETED.equals(action)) return;
        if (replaced) {
            // An update installed from this app: the install killed the service, so start it again
            // (whatever the autostart setting says, monitoring was running a moment ago).
            SharedPreferences p = PreferenceManager.getDefaultSharedPreferences(context);
            if (!p.getBoolean(UpdateManager.PREF_RESTART, false)) return;
        } else if (!AppSettings.load(context).autostart) {
            return;
        }
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
