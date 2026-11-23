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
    /**
     * Some camera drivers silently stop delivering preview frames (no error callback), e.g. after
     * the screen turned off. Frames normally arrive at least 5 times a second, so this long without
     * one means the camera is stuck: it is closed and opened again.
     */
    static final long FRAME_TIMEOUT_MS = 15_000L;
    private static final long WATCHDOG_MS = 5_000L;

    /** Notable camera events for the run-time log shown in the browser. */
    interface Events {
        void event(String text);
    }

    private final AppSettings settings;
    private final RecordingStore store;
    private final FrameHub hub;
    private final Events events;
    private final HandlerThread thread;
    private final Handler handler;

    private Camera camera;
    private PreviewSink sink;
    private int width;
    private int height;
    /** Person mode: motion area (share of the grid) that opens the gate; the shape check does the rest. */
    static final float PERSON_GATE_AREA = 0.006f;
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

    /** Person mode only (null in motion mode): see PersonGate for the rules. */
    private PersonDetector person;
    private final PersonGate gate = new PersonGate();
    private final PersonDetector.Input personInput = new PersonDetector.Input();
    private volatile long lastPersonSeenMs;
    private volatile long lastPersonWallMs;
    private volatile String personBasis = "";
    private static final long PERSON_STATUS_HOLD_MS = 10_000L;

    private volatile ThermalPolicy.Level level = ThermalPolicy.Level.NORMAL;
    private long lastAnalyzeMs;
    private long lastLiveMs;
    private long lastEncodeMs;
    private long lastStorageCheckMs;
    private long lastFrameMs;
    private int openFailures;

    // Status, read from other threads.
    private volatile boolean running;
    private volatile boolean recording;
    private volatile long lastMotionMs;
    private volatile long lastMotionWallMs;
    private volatile float motionRatio;
    private volatile String error;
    private volatile boolean storageFull;

    CameraPipeline(AppSettings settings, RecordingStore store, FrameHub hub, Events events) {
        this.settings = settings;
        this.store = store;
        this.hub = hub;
        this.events = events;
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
        handler.postDelayed(watchdog, WATCHDOG_MS);
    }

    private final Runnable watchdog = new Runnable() {
        @Override
        public void run() {
            if (camera != null && running && SystemClock.elapsedRealtime() - lastFrameMs > FRAME_TIMEOUT_MS) {
                Log.w(TAG, "no preview frames, reopening the camera");
                events.event("カメラの映像が" + FRAME_TIMEOUT_MS / 1000 + "秒止まったため、カメラを開き直しました"
                        + (recording ? "（録画中だったファイルは保存済み）" : ""));
                close();
                open();
            }
            handler.postDelayed(this, WATCHDOG_MS);
        }
    };

    /** Stops the camera, finalizes any recording and ends the thread. Blocks briefly. */
    void shutdown() {
        handler.removeCallbacks(watchdog);
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

    /** Person mode: the last check saw a person within the last few seconds. */
    boolean personPresent() {
        return person != null && lastPersonSeenMs > 0
                && SystemClock.elapsedRealtime() - lastPersonSeenMs < PERSON_STATUS_HOLD_MS;
    }

    long lastPersonWallMs() {
        return lastPersonWallMs;
    }

    String personBasis() {
        return personBasis;
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
            gate.reset();
            person = settings.personMode() ? new HybridPersonDetector(new AndroidFaceFinder(), settings.personSens) : null;
            recorder = new VideoRecorder(width, height, settings.bitrate(), settings.fps, settings.rotation,
                    settings.timestamp);

            sink = new PreviewSink(handler);
            camera.setPreviewTexture(sink.texture());
            int frameBytes = width * height * ImageFormat.getBitsPerPixel(ImageFormat.NV21) / 8;
            for (int i = 0; i < 3; i++) camera.addCallbackBuffer(new byte[frameBytes]);
            camera.setPreviewCallbackWithBuffer(this);
            camera.startPreview();
            lastFrameMs = SystemClock.elapsedRealtime();
            running = true;
            error = null;
            if (openFailures > 0) events.event("カメラを再び開けました（" + (openFailures + 1) + "回目で成功）");
            openFailures = 0;
            Log.i(TAG, "camera started " + width + "x" + height + " fps=" + (range == null ? "?" : range[1])
                    + (idleRange != null ? " (idle " + idleRange[1] + ", active " + activeRange[1] + ")" : ""));
        } catch (Exception e) {
            Log.e(TAG, "camera open failed", e);
            error = "カメラを開けません: " + e.getMessage();
            // Only the first failure of a series: retries follow every few seconds.
            if (openFailures++ == 0) events.event("カメラを開けません（ほかのアプリが使用中、または端末の制限）: " + e.getMessage());
            close();
            scheduleReopen();
        }
    }

    private void close() {
        stopRecording();
        if (person != null) {
            person.close();
            person = null;
        }
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
        events.event(err == 2 ? "ほかのアプリがカメラを使ったため切断されました（自動で再接続）"
                : "カメラエラー（" + err + "）のため再接続します");
        close();
        scheduleReopen();
    }

    @Override
    public void onPreviewFrame(byte[] data, Camera cam) {
        if (data == null) return;
        lastFrameMs = SystemClock.elapsedRealtime();
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
            // Person mode looks at the shape afterwards, so its gate may be as sensitive as it gets.
            float area = settings.motionAreaThreshold();
            if (person != null) area = Math.min(area, PERSON_GATE_AREA);
            boolean motion = detector.update(frame, width, height, area);
            motionRatio = detector.lastRatio();
            if (person != null) {
                person.observeMotion(detector.changedMask(), detector.gridW(), detector.gridH(), settings.rotation, now);
            }
            if (motion) {
                lastMotionMs = now;
                lastMotionWallMs = System.currentTimeMillis();
                if (person != null) gate.onMotion(now); // person mode: motion only opens the gate
                else if (!recorder.isRecording()) startRecording(now);
            }
        }
        if (person != null) checkPerson(frame, now, l);

        if (recorder.isRecording()) {
            long keepMs = settings.postRecordSec * 1000L;
            boolean idle = person != null ? gate.shouldStop(now, keepMs) : now - lastMotionMs > keepMs;
            if (idle) {
                if (person != null) events.event("人が見えなくなって" + settings.postRecordSec + "秒たったため、録画を止めました");
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
                    recorder.encode(frame, (now - recorder.startedAtMs()) * 1000L, System.currentTimeMillis());
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
     * Person mode: asks the detector (only while motion is recent, or every few seconds while
     * recording) and lets {@link PersonGate} decide when to start.
     */
    private void checkPerson(byte[] frame, long now, ThermalPolicy.Level l) {
        if (!gate.wantCheck(now, recorder.isRecording(), l)) return;
        PersonDetector.Input in = personInput;
        in.nowMs = now;
        in.nv21 = frame;
        in.width = width;
        in.height = height;
        in.rotation = settings.rotation;
        in.motionMask = detector.changedMask();
        in.gridW = detector.gridW();
        in.gridH = detector.gridH();
        PersonDetector.Result r = person.detect(in);
        in.nv21 = null;
        gate.onResult(now, r.person, recorder.isRecording());
        if (r.person) {
            lastPersonSeenMs = now;
            lastPersonWallMs = System.currentTimeMillis();
            personBasis = r.basis;
        }
        if (!recorder.isRecording() && gate.shouldStart(now)) {
            startRecording(now);
            if (recorder.isRecording()) {
                gate.onRecordingStarted(now);
                events.event("人を検知したため録画を開始しました（根拠: " + r.basis + "）");
            }
        }
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
            store.requestThumb(f); // thumbnail for the recordings list, made in the background
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
