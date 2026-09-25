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
        context.startActivity(new Intent(context, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }
}
