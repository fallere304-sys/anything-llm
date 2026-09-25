package com.z4motioncam;

import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.util.List;

/**
 * Camera -> (motion detection, recording, live JPEG) on one background thread.
 *
 * <p>Every preview frame is inspected, but work is rate-limited per consumer: motion analysis a
 * few times per second, encoding at the recording frame rate only while recording, and JPEG
 * compression only while somebody is watching.
 */
@SuppressWarnings("deprecation") // Camera1 is the simplest and lightest API on Android 5-7.
final class CameraPipeline implements Camera.PreviewCallback, Camera.ErrorCallback {
    private static final String TAG = "CameraPipeline";
    private static final int JPEG_QUALITY = 60;
    private static final long STORAGE_CHECK_MS = 15_000L;
    private static final long REOPEN_DELAY_MS = 3_000L;

    private final AppSettings settings;
    private final RecordingStore store;
    private final FrameHub hub;
    private final HandlerThread thread;
    private final Handler handler;

    private Camera camera;
    private PreviewSink sink;
    private int width;
    private int height;
    private MotionDetector detector;
    private VideoRecorder recorder;
    private final ByteArrayOutputStream jpegBuf = new ByteArrayOutputStream(64 * 1024);
    private Rect frameRect;

    private volatile ThermalPolicy.Level level = ThermalPolicy.Level.NORMAL;
    private long lastAnalyzeMs;
    private long lastLiveMs;
    private long lastEncodeMs;
    private long lastStorageCheckMs;

    // Status, read from other threads.
    private volatile boolean running;
    private volatile boolean recording;
    private volatile long lastMotionMs;
    private volatile long lastMotionWallMs;
    private volatile float motionRatio;
    private volatile String error;
    private volatile boolean storageFull;

    CameraPipeline(AppSettings settings, RecordingStore store, FrameHub hub) {
        this.settings = settings;
        this.store = store;
        this.hub = hub;
        this.thread = new HandlerThread("camera");
        thread.start();
        this.handler = new Handler(thread.getLooper());
    }

    void start() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                open();
            }
        });
    }

    /** Stops the camera, finalizes any recording and ends the thread. Blocks briefly. */
    void shutdown() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                close();
                thread.quit();
            }
        });
        try {
            thread.join(5_000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    void setThermalLevel(ThermalPolicy.Level l) {
        level = l;
    }

    boolean isRunning() {
        return running;
    }

    boolean isRecording() {
        return recording;
    }

    float motionRatio() {
        return motionRatio;
    }

    long lastMotionWallMs() {
        return lastMotionWallMs;
    }

    String error() {
        return error;
    }

    boolean storageFull() {
        return storageFull;
    }

    // ---- camera thread only below ----

    private void open() {
        try {
            int id = findBackCamera();
            camera = Camera.open(id);
            camera.setErrorCallback(this);
            Camera.Parameters p = camera.getParameters();
            Camera.Size size = chooseSize(p.getSupportedPreviewSizes(), settings.width, settings.height);
            p.setPreviewSize(size.width, size.height);
            p.setPreviewFormat(ImageFormat.NV21);
            int[] range = chooseFpsRange(p.getSupportedPreviewFpsRange(), settings.fps * 1000);
            if (range != null) p.setPreviewFpsRange(range[0], range[1]);
            List<String> focus = p.getSupportedFocusModes();
            if (focus != null && focus.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO)) {
                p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO);
            }
            List<String> flash = p.getSupportedFlashModes();
            if (flash != null && flash.contains(Camera.Parameters.FLASH_MODE_OFF)) {
                p.setFlashMode(Camera.Parameters.FLASH_MODE_OFF);
            }
            if (p.isVideoStabilizationSupported()) p.setVideoStabilization(false);
            camera.setParameters(p);

            width = size.width;
            height = size.height;
            frameRect = new Rect(0, 0, width, height);
            detector = MotionDetector.forFrame(width, height);
            recorder = new VideoRecorder(width, height, settings.bitrate(), settings.fps, settings.rotation);

            sink = new PreviewSink(handler);
            camera.setPreviewTexture(sink.texture());
            int frameBytes = width * height * ImageFormat.getBitsPerPixel(ImageFormat.NV21) / 8;
            for (int i = 0; i < 3; i++) camera.addCallbackBuffer(new byte[frameBytes]);
            camera.setPreviewCallbackWithBuffer(this);
            camera.startPreview();
            running = true;
            error = null;
            Log.i(TAG, "camera started " + width + "x" + height + " fps=" + (range == null ? "?" : range[1]));
        } catch (Exception e) {
            Log.e(TAG, "camera open failed", e);
            error = "カメラを開けません: " + e.getMessage();
            close();
            scheduleReopen();
        }
    }

    private void close() {
        stopRecording();
        if (camera != null) {
            try {
                camera.setPreviewCallbackWithBuffer(null);
                camera.stopPreview();
            } catch (RuntimeException ignored) {
                // camera already dead
            }
            camera.release();
            camera = null;
        }
        if (sink != null) {
            sink.release();
            sink = null;
        }
        running = false;
        hub.clear();
    }

    private void scheduleReopen() {
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (camera == null) open();
            }
        }, REOPEN_DELAY_MS);
    }

    @Override
    public void onError(int err, Camera cam) {
        Log.e(TAG, "camera error " + err);
        error = "カメラエラー (" + err + ")、再接続中";
        close();
        scheduleReopen();
    }

    @Override
    public void onPreviewFrame(byte[] data, Camera cam) {
        if (data == null) return;
        try {
            processFrame(data, SystemClock.elapsedRealtime());
        } catch (RuntimeException e) {
            Log.e(TAG, "frame processing failed", e);
            error = "処理エラー: " + e.getMessage();
            stopRecording();
        } finally {
            if (camera != null) camera.addCallbackBuffer(data);
        }
    }

    private void processFrame(byte[] frame, long now) {
        ThermalPolicy.Level l = level;

        if (now - lastAnalyzeMs >= ThermalPolicy.analyzeIntervalMs(l)) {
            lastAnalyzeMs = now;
            boolean motion = detector.update(frame, width, height, settings.motionAreaThreshold());
            motionRatio = detector.lastRatio();
            if (motion) {
                lastMotionMs = now;
                lastMotionWallMs = System.currentTimeMillis();
                if (!recorder.isRecording()) startRecording(now);
            }
        }

        if (recorder.isRecording()) {
            if (now - lastMotionMs > settings.postRecordSec * 1000L) {
                stopRecording();
            } else {
                if (now - recorder.startedAtMs() > settings.segmentMin * 60_000L) {
                    stopRecording();
                    startRecording(now);
                } else if (now - lastStorageCheckMs > STORAGE_CHECK_MS) {
                    lastStorageCheckMs = now;
                    if (!store.ensureFreeSpace()) {
                        storageFull = true;
                        stopRecording();
                    }
                }
                long frameInterval = 1000L / ThermalPolicy.recordFps(l, settings.fps);
                // Small tolerance so camera timing jitter does not halve the frame rate.
                if (recorder.isRecording() && now - lastEncodeMs >= frameInterval - 15) {
                    lastEncodeMs = now;
                    recorder.encode(frame, (now - recorder.startedAtMs()) * 1000L);
                }
            }
        }

        if (hub.isWanted(System.currentTimeMillis()) && now - lastLiveMs >= ThermalPolicy.liveIntervalMs(l)) {
            lastLiveMs = now;
            jpegBuf.reset();
            new YuvImage(frame, ImageFormat.NV21, width, height, null)
                    .compressToJpeg(frameRect, JPEG_QUALITY, jpegBuf);
            hub.publish(jpegBuf.toByteArray());
        }
    }

    private void startRecording(long now) {
        lastStorageCheckMs = now;
        if (!store.ensureFreeSpace()) {
            storageFull = true;
            error = "空き容量不足のため録画できません";
            return;
        }
        storageFull = false;
        try {
            File part = store.newPartFile(System.currentTimeMillis());
            recorder.start(part, now);
            lastEncodeMs = 0;
            recording = true;
            Log.i(TAG, "recording " + part.getName());
        } catch (Exception e) {
            Log.e(TAG, "recorder start failed", e);
            error = "録画を開始できません: " + e.getMessage();
        }
    }

    private void stopRecording() {
        if (recorder != null && recorder.isRecording()) {
            File f = recorder.stop();
            Log.i(TAG, "recording finished " + (f == null ? "(empty)" : f.getName()));
        }
        recording = false;
    }

    private static int findBackCamera() {
        Camera.CameraInfo info = new Camera.CameraInfo();
        for (int i = 0; i < Camera.getNumberOfCameras(); i++) {
            Camera.getCameraInfo(i, info);
            if (info.facing == Camera.CameraInfo.CAMERA_FACING_BACK) return i;
        }
        return 0;
    }

    /** Exact match if available, otherwise the supported size closest in pixel count. */
    private static Camera.Size chooseSize(List<Camera.Size> sizes, int w, int h) {
        Camera.Size best = sizes.get(0);
        long bestDiff = Long.MAX_VALUE;
        for (Camera.Size s : sizes) {
            if (s.width == w && s.height == h) return s;
            // Stay on 16-pixel aligned sizes: avoids stride padding quirks in hardware encoders.
            if (s.width % 16 != 0 || s.height % 16 != 0) continue;
            long diff = Math.abs((long) s.width * s.height - (long) w * h);
            if (diff < bestDiff) {
                bestDiff = diff;
                best = s;
            }
        }
        return best;
    }

    /**
     * The range whose maximum is closest to the target, preferring the lowest minimum: a low
     * minimum lets auto exposure use longer shutter times in a dark nursery.
     */
    static int[] chooseFpsRange(List<int[]> ranges, int targetMilliFps) {
        if (ranges == null || ranges.isEmpty()) return null;
        int[] best = null;
        for (int[] r : ranges) {
            if (best == null) {
                best = r;
                continue;
            }
            long d = Math.abs(r[1] - targetMilliFps);
            long bd = Math.abs(best[1] - targetMilliFps);
            if (d < bd || (d == bd && r[0] < best[0])) best = r;
        }
        return best;
    }
}
