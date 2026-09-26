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
    /**
     * A file is continued in a new one beyond this size even when splitting is off: Android's MP4
     * writer uses 32-bit offsets, so files must stay well below 4 GB (about 7 hours at 640x480).
     */
    static final long MAX_FILE_BYTES = 3_500_000_000L;
    private static final long REOPEN_DELAY_MS = 3_000L;
    /** Sensor rate while only watching for motion (nobody viewing, not recording). */
    private static final int IDLE_FPS = 5;
    /** Stay at the full rate this long after the last viewer / recording, to avoid flapping. */
    private static final long ACTIVE_HOLD_MS = 5_000L;

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
    /** Live JPEGs are made from a half-size copy for 1280-wide and larger frames. */
    private int liveScale;
    private byte[] liveFrame;
    private Rect liveRect;
    private int[] idleRange;
    private int[] activeRange;
    private int[] currentRange;
    private long lastActiveMs;

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
            List<int[]> ranges = p.getSupportedPreviewFpsRange();
            activeRange = chooseFpsRange(ranges, settings.fps * 1000);
            idleRange = chooseFpsRange(ranges, Math.min(IDLE_FPS, settings.fps) * 1000);
            // Only worth switching when idle really runs the sensor slower.
            if (activeRange == null || idleRange == null || idleRange[1] >= activeRange[1]) idleRange = null;
            int[] range = idleRange != null ? idleRange : activeRange;
            if (range != null) p.setPreviewFpsRange(range[0], range[1]);
            currentRange = range;
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
            liveScale = width >= 1280 ? 2 : 1;
            liveFrame = liveScale == 1 ? null : new byte[(width / 2) * (height / 2) * 3 / 2];
            liveRect = new Rect(0, 0, width / liveScale, height / liveScale);
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
            Log.i(TAG, "camera started " + width + "x" + height + " fps=" + (range == null ? "?" : range[1])
                    + (idleRange != null ? " (idle " + idleRange[1] + ", active " + activeRange[1] + ")" : ""));
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
                boolean timeUp = settings.segmentMin > 0 && now - recorder.startedAtMs() > settings.segmentMin * 60_000L;
                if (timeUp) {
                    stopRecording();
                    startRecording(now);
                } else if (now - lastStorageCheckMs > STORAGE_CHECK_MS) {
                    lastStorageCheckMs = now;
                    if (!store.ensureFreeSpace()) {
                        storageFull = true;
                        stopRecording();
                    } else if (recorder.partFile() != null && recorder.partFile().length() > MAX_FILE_BYTES) {
                        stopRecording();
                        startRecording(now);
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

        boolean wanted = hub.isWanted(System.currentTimeMillis());
        if (wanted && now - lastLiveMs >= ThermalPolicy.liveIntervalMs(l)) {
            lastLiveMs = now;
            jpegBuf.reset();
            byte[] src = frame;
            if (liveScale == 2) {
                downscale2x(frame, width, height, liveFrame);
                src = liveFrame;
            }
            new YuvImage(src, ImageFormat.NV21, liveRect.width(), liveRect.height(), null)
                    .compressToJpeg(liveRect, JPEG_QUALITY, jpegBuf);
            hub.publish(jpegBuf.toByteArray());
        }

        updateSensorRate(wanted || recorder.isRecording(), l, now);
    }

    /**
     * Runs the sensor at the low idle rate while it only watches for motion, and at the configured
     * rate while recording or while someone is watching. When hot, it stays at the idle rate.
     */
    private void updateSensorRate(boolean busy, ThermalPolicy.Level l, long now) {
        if (idleRange == null) return;
        if (busy) lastActiveMs = now;
        boolean hot = l.ordinal() >= ThermalPolicy.Level.HOT.ordinal();
        int[] want = !hot && now - lastActiveMs < ACTIVE_HOLD_MS ? activeRange : idleRange;
        if (want == currentRange) return;
        try {
            Camera.Parameters p = camera.getParameters();
            p.setPreviewFpsRange(want[0], want[1]);
            camera.setParameters(p);
            currentRange = want;
        } catch (RuntimeException e) {
            // Some camera drivers refuse changing the rate during preview: keep one fixed rate.
            Log.w(TAG, "fps switch not supported", e);
            idleRange = null;
        }
    }

    /** Nearest-neighbour 2x downscale of an NV21 frame (even dimensions). */
    static void downscale2x(byte[] src, int w, int h, byte[] dst) {
        int w2 = w / 2;
        int h2 = h / 2;
        for (int y = 0; y < h2; y++) {
            int s = 2 * y * w;
            int d = y * w2;
            for (int x = 0; x < w2; x++) dst[d + x] = src[s + 2 * x];
        }
        int srcUv = w * h;
        int dstUv = w2 * h2;
        for (int r = 0; r < h2 / 2; r++) {
            int s = srcUv + 2 * r * w;
            int d = dstUv + r * w2;
            for (int c = 0; c < w2; c += 2) {
                dst[d + c] = src[s + 2 * c];         // V
                dst[d + c + 1] = src[s + 2 * c + 1]; // U
            }
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
            // Fallbacks stay on 16-aligned, even sizes: avoids encoder padding quirks.
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
     * The range with the smallest maximum that still reaches the target (the sensor then runs no
     * faster than needed), preferring the lowest minimum so auto exposure can use long shutter
     * times in a dark nursery. If nothing reaches the target, the fastest range.
     */
    static int[] chooseFpsRange(List<int[]> ranges, int targetMilliFps) {
        if (ranges == null || ranges.isEmpty()) return null;
        int[] best = null;
        int[] fastest = null;
        for (int[] r : ranges) {
            if (fastest == null || r[1] > fastest[1]) fastest = r;
            if (r[1] < targetMilliFps) continue;
            if (best == null || r[1] < best[1] || (r[1] == best[1] && r[0] < best[0])) best = r;
        }
        return best != null ? best : fastest;
    }
}
