package app.mojiokoshi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * マイク録音と音声認識を行うフォアグラウンドサービス。
 * 画面が暗い / 消灯していても 2 時間以上止まらずに動き続けることを目的にしている。
 *
 * 録音スレッドと認識スレッドを分け、キューで繋いでいる。
 * モデル読み込み中や認識が一時的に遅れても録音は途切れず、後から追いつく。
 * 確定した文は 1 文ごとにテキストファイルへ追記するので、途中で落ちても内容は残る。
 */
public class TranscriptionService extends Service {
    private static final String TAG = "TranscriptionService";

    static final String ACTION_START = "app.mojiokoshi.START";
    static final String ACTION_STOP = "app.mojiokoshi.STOP";

    private static final int SAMPLE_RATE = 16000;
    /** 0.2 秒ぶん */
    private static final int CHUNK_SAMPLES = SAMPLE_RATE / 5;
    private static final short[] END_OF_STREAM = new short[0];
    private static final int NOTIFICATION_ID = 1;
    private static final String CHANNEL_ID = "transcription";
    private static final long PARTIAL_UPDATE_INTERVAL_MS = 300;
    /** 保険として wake lock には上限を付ける (2 時間の要件に対して十分な余裕) */
    private static final long WAKE_LOCK_TIMEOUT_MS = 6 * 60 * 60 * 1000L;

    enum Phase { IDLE, RUNNING, FINISHING, FINISHED }

    /** 画面側へ状態変化を伝える (常にメインスレッドで呼ばれる)。 */
    interface Listener {
        void onTranscriptionChanged();
    }

    /** 画面表示用の状態のコピー。 */
    static final class Snapshot {
        Phase phase;
        boolean modelReady;
        long startElapsedMs;
        long endElapsedMs;
        String text;
        String partial;
        File file;
        String error;
    }

    // ---- プロセス内で共有する状態 (LOCK で保護) ----
    private static final Object LOCK = new Object();
    private static Phase phase = Phase.IDLE;
    private static boolean modelReady;
    private static long startElapsedMs;
    private static long endElapsedMs;
    private static final StringBuilder text = new StringBuilder();
    private static String partial = "";
    private static File outputFile;
    private static String error;
    private static Listener listener;

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final Runnable NOTIFY = new Runnable() {
        @Override
        public void run() {
            Listener l;
            synchronized (LOCK) {
                l = listener;
            }
            if (l != null) {
                l.onTranscriptionChanged();
            }
        }
    };

    static void setListener(Listener l) {
        synchronized (LOCK) {
            listener = l;
        }
    }

    static Snapshot snapshot() {
        synchronized (LOCK) {
            Snapshot s = new Snapshot();
            s.phase = phase;
            s.modelReady = modelReady;
            s.startElapsedMs = startElapsedMs;
            s.endElapsedMs = endElapsedMs;
            s.text = text.toString();
            s.partial = partial;
            s.file = outputFile;
            s.error = error;
            return s;
        }
    }

    /** 結果を表示し終えたら呼ぶ。次の文字起こしを開始できる状態に戻す。 */
    static void acknowledgeResult() {
        synchronized (LOCK) {
            if (phase == Phase.FINISHED) {
                phase = Phase.IDLE;
                text.setLength(0);
                partial = "";
                outputFile = null;
                error = null;
            }
        }
    }

    static void start(Context context) {
        Intent i = new Intent(context, TranscriptionService.class).setAction(ACTION_START);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(i);
        } else {
            context.startService(i);
        }
    }

    static void stop(Context context) {
        context.startService(new Intent(context, TranscriptionService.class).setAction(ACTION_STOP));
    }

    private static void notifyChanged() {
        MAIN.removeCallbacks(NOTIFY);
        MAIN.post(NOTIFY);
    }

    // ---- サービス本体 ----

    private final BlockingQueue<short[]> queue = new LinkedBlockingQueue<>();
    private volatile boolean stopRequested;
    private PowerManager.WakeLock wakeLock;
    private Thread recordThread;
    private Thread recognizeThread;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_START.equals(action)) {
            startTranscription();
        } else if (ACTION_STOP.equals(action)) {
            requestStop();
        }
        synchronized (LOCK) {
            if (phase != Phase.RUNNING && phase != Phase.FINISHING && recordThread == null) {
                // 何も動いていないのに起こされた (STOP の二重送信など)
                stopSelf();
            }
        }
        return START_NOT_STICKY;
    }

    private void startTranscription() {
        synchronized (LOCK) {
            if (phase == Phase.RUNNING || phase == Phase.FINISHING) {
                return;
            }
            phase = Phase.RUNNING;
            modelReady = ModelManager.isReady();
            startElapsedMs = SystemClock.elapsedRealtime();
            endElapsedMs = 0;
            text.setLength(0);
            partial = "";
            error = null;
            outputFile = createOutputFile();
        }
        startForegroundCompat();

        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mojiokoshi:transcription");
        wakeLock.acquire(WAKE_LOCK_TIMEOUT_MS);

        stopRequested = false;
        queue.clear();
        recordThread = new Thread(new Runnable() {
            @Override
            public void run() {
                recordLoop();
            }
        }, "audio-record");
        recognizeThread = new Thread(new Runnable() {
            @Override
            public void run() {
                recognizeLoop();
            }
        }, "vosk-recognize");
        recordThread.setPriority(Thread.MAX_PRIORITY);
        recordThread.start();
        recognizeThread.start();
        notifyChanged();
    }

    private void requestStop() {
        synchronized (LOCK) {
            if (phase != Phase.RUNNING) {
                return;
            }
            phase = Phase.FINISHING;
            endElapsedMs = SystemClock.elapsedRealtime();
        }
        stopRequested = true;
        notifyChanged();
    }

    /** 録音スレッド: マイクから 0.2 秒ずつ読み、キューへ流す。 */
    private void recordLoop() {
        AudioRecord recorder = null;
        try {
            int minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            // 1 秒ぶん以上のバッファを確保して取りこぼしを防ぐ
            int bufBytes = Math.max(minBuf, SAMPLE_RATE * 2);
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufBytes);
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                fail("マイクを初期化できません (他のアプリが使用中の可能性があります)");
                return;
            }
            recorder.startRecording();
            short[] buf = new short[CHUNK_SAMPLES];
            while (!stopRequested) {
                int n = recorder.read(buf, 0, buf.length);
                if (n > 0) {
                    queue.offer(Arrays.copyOf(buf, n));
                } else if (n < 0) {
                    fail("録音エラー (" + n + ")");
                    return;
                }
            }
        } catch (RuntimeException e) {
            Log.e(TAG, "record failed", e);
            fail("録音エラー: " + e.getMessage());
        } finally {
            if (recorder != null) {
                try {
                    recorder.stop();
                } catch (IllegalStateException ignored) {
                    // 録音開始前に失敗した場合
                }
                recorder.release();
            }
            queue.offer(END_OF_STREAM);
        }
    }

    /** 認識スレッド: キューの音声を Vosk に渡し、確定した文をファイルと画面へ出す。 */
    private void recognizeLoop() {
        Writer writer = null;
        Recognizer recognizer = null;
        try {
            File file;
            synchronized (LOCK) {
                file = outputFile;
            }
            if (file != null) {
                writer = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(file, true), "UTF-8"));
            }

            Model model = ModelManager.await(this);
            synchronized (LOCK) {
                modelReady = true;
            }
            notifyChanged();

            recognizer = new Recognizer(model, SAMPLE_RATE);
            long lastPartialAt = 0;
            while (true) {
                short[] chunk = queue.take();
                if (chunk == END_OF_STREAM) {
                    break;
                }
                if (recognizer.acceptWaveForm(chunk, chunk.length)) {
                    appendFinal(recognizer.getResult(), writer);
                } else {
                    long now = SystemClock.elapsedRealtime();
                    if (now - lastPartialAt >= PARTIAL_UPDATE_INTERVAL_MS) {
                        lastPartialAt = now;
                        String p = TextFormatter.clean(field(recognizer.getPartialResult(), "partial"));
                        synchronized (LOCK) {
                            partial = p;
                        }
                        notifyChanged();
                    }
                }
            }
            appendFinal(recognizer.getFinalResult(), writer);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (IOException | RuntimeException e) {
            Log.e(TAG, "recognize failed", e);
            fail("音声認識エラー: " + e.getMessage());
            // 録音スレッドの終了を待ってキューを空にする
            drainUntilEnd();
        } finally {
            if (recognizer != null) {
                recognizer.close();
            }
            if (writer != null) {
                try {
                    writer.close();
                } catch (IOException ignored) {
                    // 追記済みの内容は flush 済み
                }
            }
            finish();
        }
    }

    private void drainUntilEnd() {
        try {
            while (queue.take() != END_OF_STREAM) {
                // 捨てる
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void appendFinal(String json, Writer writer) throws IOException {
        String line = TextFormatter.clean(field(json, "text"));
        synchronized (LOCK) {
            partial = "";
            if (!line.isEmpty()) {
                text.append(line).append('\n');
            }
        }
        if (!line.isEmpty() && writer != null) {
            writer.write(line);
            writer.write('\n');
            writer.flush();
        }
        notifyChanged();
    }

    private void fail(String message) {
        synchronized (LOCK) {
            if (error == null) {
                error = message;
            }
            if (phase == Phase.RUNNING) {
                phase = Phase.FINISHING;
                endElapsedMs = SystemClock.elapsedRealtime();
            }
        }
        stopRequested = true;
        notifyChanged();
    }

    private void finish() {
        synchronized (LOCK) {
            phase = Phase.FINISHED;
            partial = "";
            if (endElapsedMs == 0) {
                endElapsedMs = SystemClock.elapsedRealtime();
            }
        }
        MAIN.post(new Runnable() {
            @Override
            public void run() {
                releaseWakeLock();
                recordThread = null;
                recognizeThread = null;
                stopForeground(true);
                stopSelf();
            }
        });
        notifyChanged();
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        wakeLock = null;
    }

    @Override
    public void onDestroy() {
        // システムにより終了させられた場合も録音は止めておく
        stopRequested = true;
        releaseWakeLock();
        super.onDestroy();
    }

    private File createOutputFile() {
        File dir = getExternalFilesDir("transcripts");
        if (dir == null || (!dir.exists() && !dir.mkdirs())) {
            dir = new File(getFilesDir(), "transcripts");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
        }
        String name = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
        return new File(dir, "mojiokoshi_" + name + ".txt");
    }

    private void startForegroundCompat() {
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private Notification buildNotification() {
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(CHANNEL_ID,
                    getString(R.string.notification_channel), NotificationManager.IMPORTANCE_LOW);
            nm.createNotificationChannel(ch);
            b = new Notification.Builder(this, CHANNEL_ID);
        } else {
            b = new Notification.Builder(this).setPriority(Notification.PRIORITY_LOW);
        }
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), flags);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, TranscriptionService.class).setAction(ACTION_STOP), flags);
        return b.setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(getString(R.string.notification_text))
                .setContentIntent(open)
                .setOngoing(true)
                .setShowWhen(true)
                .setUsesChronometer(true)
                .addAction(new Notification.Action.Builder(0, getString(R.string.stop), stop).build())
                .build();
    }

    private static String field(String json, String key) {
        if (json == null) {
            return "";
        }
        try {
            return new JSONObject(json).optString(key, "");
        } catch (JSONException e) {
            return "";
        }
    }
}
