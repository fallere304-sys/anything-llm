package com.z4motioncam;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Status screen. Shows a low-rate preview and status for 30 s after a touch, then drops the
 * backlight to its minimum over an all-black window and stops all screen updates. Any touch
 * brings it back (the waking touch is swallowed so it cannot press a button by accident).
 */
public class MainActivity extends Activity {
    private static final int REQ_CAMERA = 1;
    private static final long REFRESH_MS = 500L;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private ImageView preview;
    private TextView status;
    private View blackout;
    private CameraService service;
    private boolean dimmed;
    private boolean swallowGesture;
    private boolean resumed;
    private boolean bound;
    private long shownSeq = -1;
    private int tick;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = ((CameraService.LocalBinder) binder).service();
            refresh.run();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            service = null;
        }
    };

    private final Runnable dim = new Runnable() {
        @Override
        public void run() {
            setDimmed(true);
        }
    };

    private final Runnable refresh = new Runnable() {
        @Override
        public void run() {
            ui.removeCallbacks(this);
            if (dimmed || !resumed || service == null) return;
            // The status lists the recordings directory, so refresh it less often than the image.
            if (tick++ % 4 == 0) status.setText(service.statusText());
            FrameHub hub = service.frameHub();
            hub.request(System.currentTimeMillis());
            FrameHub.Frame f = hub.latest();
            if (f != null && f.seq != shownSeq) {
                shownSeq = f.seq;
                // Live JPEGs are at most 960 px wide (large frames are halved), so decode as is.
                Bitmap b = BitmapFactory.decodeByteArray(f.jpeg, 0, f.jpeg.length);
                if (b != null) preview.setImageBitmap(b);
            }
            preview.setRotation(service.settings().rotation);
            ui.postDelayed(this, REFRESH_MS);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);
        setContentView(R.layout.activity_main);
        preview = (ImageView) findViewById(R.id.preview);
        status = (TextView) findViewById(R.id.status);
        blackout = findViewById(R.id.blackout);
        findViewById(R.id.btn_settings).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, SettingsActivity.class));
            }
        });
        findViewById(R.id.btn_stop).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                stopService(new Intent(MainActivity.this, CameraService.class));
                finish();
            }
        });

        if (hasCameraPermission()) {
            startMonitoring();
        } else if (Build.VERSION.SDK_INT >= 23) {
            requestPermissions(new String[] {Manifest.permission.CAMERA}, REQ_CAMERA);
        }
    }

    private boolean hasCameraPermission() {
        return Build.VERSION.SDK_INT < 23
                || checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode != REQ_CAMERA) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            startMonitoring();
        } else {
            Toast.makeText(this, R.string.permission_denied, Toast.LENGTH_LONG).show();
            finish();
        }
    }

    private void startMonitoring() {
        // RELOAD applies settings changed in SettingsActivity; it is a no-op when nothing changed.
        startService(new Intent(this, CameraService.class).setAction(CameraService.ACTION_RELOAD));
        if (!bound) bound = bindService(new Intent(this, CameraService.class), connection, Context.BIND_AUTO_CREATE);
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        hideSystemBars();
        if (hasCameraPermission() && service != null) {
            startService(new Intent(this, CameraService.class).setAction(CameraService.ACTION_RELOAD));
        }
        setDimmed(false);
    }

    @Override
    protected void onPause() {
        resumed = false;
        ui.removeCallbacks(dim);
        ui.removeCallbacks(refresh);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (bound) unbindService(connection);
        bound = false;
        service = null;
        super.onDestroy();
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent ev) {
        int action = ev.getActionMasked();
        if (dimmed && action == MotionEvent.ACTION_DOWN) {
            setDimmed(false);
            swallowGesture = true;
        }
        if (swallowGesture) {
            if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) swallowGesture = false;
            return true;
        }
        ui.removeCallbacks(dim);
        ui.postDelayed(dim, AppSettings.DIM_TIMEOUT_MS);
        return super.dispatchTouchEvent(ev);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    private void setDimmed(boolean dim) {
        dimmed = dim;
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        // 0 = lowest backlight the platform allows; public APIs cannot switch the panel fully off
        // while keeping touch input alive.
        lp.screenBrightness = dim ? 0f : WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        lp.buttonBrightness = dim ? 0f : WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        getWindow().setAttributes(lp);
        blackout.setVisibility(dim ? View.VISIBLE : View.GONE);
        ui.removeCallbacks(this.dim);
        if (dim) {
            ui.removeCallbacks(refresh);
            preview.setImageDrawable(null); // free the bitmap; nothing is drawn while dark
            shownSeq = -1;
        } else {
            ui.postDelayed(this.dim, AppSettings.DIM_TIMEOUT_MS);
            tick = 0;
            refresh.run();
        }
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }
}
