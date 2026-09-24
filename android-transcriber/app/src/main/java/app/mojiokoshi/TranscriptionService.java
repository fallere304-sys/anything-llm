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

import com.k2fsa.sherpa.onnx.OfflineRecognizer;
import com.k2fsa.sherpa.onnx.OfflineStream;
import com.k2fsa.sherpa.onnx.SpeechSegment;
import com.k2fsa.sherpa.onnx.Vad;

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
 * 認識スレッドは Silero VAD で発話の区切りを検出し、1 発話ごとに ReazonSpeech モデルで文字にする。
 * モデル読み込み中や認識が一時的に遅れても録音は途切れず、後から追いつく。
 * 音声はメモリ上でのみ扱い、認識後に捨てる (ファイルには残さない)。
 * 確定した文は 1 文ごとにテキストファイルへ追記するので、途中で落ちても内容は残る。
 */
public class TranscriptionService extends Service {
    private static final String TAG = "TranscriptionService";

    static final String ACTION_START = "app.mojiokoshi.START";
    static final String ACTION_STOP = "app.mojiokoshi.STOP";

    private static final int SAMPLE_RATE = ModelManager.SAMPLE_RATE;
    /** 発話区切りの検出は少し遅れるので、発話の頭が欠けないよう手前 0.5 秒も認識に含める */
    private static final int SEGMENT_PAD_SAMPLES = SAMPLE_RATE / 2;
    /** 発話の頭を補うために保持しておく直近の音声 (1 発話の上限 20 秒 + 余裕) */
    private static final int HISTORY_SAMPLES = SAMPLE_RATE * 30;
    /** 0.2 秒ぶん */
    private static final int CHUNK_SAMPLES = SAMPLE_RATE / 5;
    private static final short[] END_OF_STREAM = new short[0];
    private static final int NOTIFICATION_ID = 1;
    private static final String CHANNEL_ID = "transcription";
    private static final long PARTIAL_UPDATE_INTERVAL_MS = 500;
    /** 発話中に画面へ出す目印 (このモデルは発話が終わるまで文字を出さない) */
    private static final String SPEAKING_MARK = "…";
    /** 保険として wake lock には上限を付ける (2 時間の要件に対して十分な余裕) */
    private static final long WAKE_LOCK_TIMEOUT_MS = 6 * 60 * 60 * 1000L;
    /**
     * 認識待ち音声の上限 (5 分 = 1500 チャンク、約 10MB)。
     * 初回のモデル展開 (数十秒) の間の音声は十分に溜められ、
     * 認識が録音に追いつかない状態が続いてもメモリを使い切らない。
     */
    private static final int MAX_QUEUED_CHUNKS = 5 * 60 * 5;
    /** 録音中の画面に渡す直近の行数 */
    private static final int RECENT_LINES = 8;

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
        /** 全文。コピーのコストを避けるため FINISHED のときだけ入る (それ以外は "") */
        String text;
        /** 直近 RECENT_LINES 行 (録音中のプレビュー用) */
        String recent;
        String partial;
        File file;
        String error;
        /** 致命的ではない問題 (音声の欠落・ファイル保存失敗)。無ければ null */
        String warning;
        /** 認識待ちの音声の長さ (ミリ秒)。端末の処理が追いついているかの目安 */
        long backlogMs;
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
    private static long droppedSamples;
    private static boolean saveFailed;
    private static long backlogMs;
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
            // 2 時間分の全文 (数十万文字) を 0.3 秒ごとに複製しないよう、録音中は末尾だけ渡す
            s.text = phase == Phase.FINISHED ? text.toString() : "";
            s.recent = tail(text, RECENT_LINES);
            s.partial = partial;
            s.file = outputFile;
            s.error = error;
            s.warning = warningLocked();
            s.backlogMs = backlogMs;
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
                droppedSamples = 0;
                saveFailed = false;
            }
        }
    }

    private static String warningLocked() {
        StringBuilder w = new StringBuilder();
        if (droppedSamples > 0) {
            w.append("処理が追いつかず、約").append(droppedSamples / SAMPLE_RATE)
                    .append("秒分の音声を認識できませんでした");
        }
        if (saveFailed) {
            if (w.length() > 0) {
                w.append('\n');
            }
            w.append("ファイルへの保存に失敗しました (空き容量不足など)。画面のテキストをコピーしてください");
        }
        return w.length() > 0 ? w.toString() : null;
    }

    /** text の末尾 lines 行を返す (各行は '\n' で終わる)。 */
    private static String tail(CharSequence text, int lines) {
        int end = text.length();
        int found = 0;
        int i;
        for (i = end - 1; i >= 0; i--) {
            // 末尾の改行は最終行の終端なので数えない
            if (text.charAt(i) == '\n' && i != end - 1 && ++found == lines) {
                break;
            }
        }
        return text.subSequence(i + 1, end).toString();
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

    // END_OF_STREAM を必ず入れられるよう 1 つ余分に確保する
    private final BlockingQueue<short[]> queue = new LinkedBlockingQueue<>(MAX_QUEUED_CHUNKS + 1);
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
            droppedSamples = 0;
            saveFailed = false;
            backlogMs = 0;
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
        }, "asr-recognize");
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
                    enqueue(Arrays.copyOf(buf, n));
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

    /**
     * 認識待ちが上限を超えたら最も古い音声を捨てる。
     * 欠落は避けられないが、画面に出る内容を「今の発言」に近く保ち、欠落量は結果画面で知らせる。
     */
    private void enqueue(short[] chunk) {
        while (queue.size() >= MAX_QUEUED_CHUNKS) {
            short[] old = queue.poll();
            if (old == null) {
                break;
            }
            synchronized (LOCK) {
                droppedSamples += old.length;
            }
        }
        queue.offer(chunk);
    }

    /**
     * 認識スレッド: キューの音声を発話ごとに区切って認識し、確定した文をファイルと画面へ出す。
     * 音声は発話の区切りまでメモリに溜め、認識したら捨てる。
     */
    private void recognizeLoop() {
        Writer writer = null;
        Vad vad = null;
        try {
            File file;
            synchronized (LOCK) {
                file = outputFile;
            }
            if (file != null) {
                writer = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(file, true), "UTF-8"));
            }

            OfflineRecognizer recognizer = ModelManager.await(this);
            vad = ModelManager.createVad(this);
            synchronized (LOCK) {
                modelReady = true;
            }
            notifyChanged();

            Writer[] out = {writer};
            Segmenter segmenter = new Segmenter(vad, recognizer, out);
            long lastPartialAt = 0;
            while (true) {
                short[] chunk = queue.take();
                if (chunk == END_OF_STREAM) {
                    break;
                }
                segmenter.accept(chunk);
                long now = SystemClock.elapsedRealtime();
                if (now - lastPartialAt >= PARTIAL_UPDATE_INTERVAL_MS) {
                    lastPartialAt = now;
                    String p = vad.isSpeechDetected() ? SPEAKING_MARK : "";
                    synchronized (LOCK) {
                        partial = p;
                        backlogMs = queue.size() * 1000L * CHUNK_SAMPLES / SAMPLE_RATE;
                    }
                    notifyChanged();
                }
            }
            segmenter.finish();
            writer = out[0];
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (IOException | RuntimeException e) {
            Log.e(TAG, "recognize failed", e);
            fail("音声認識エラー: " + e.getMessage());
            // 録音スレッドの終了を待ってキューを空にする
            drainUntilEnd();
        } finally {
            if (vad != null) {
                vad.release();
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

    /** 音声を VAD に流し、区切られた発話を 1 つずつ認識する。 */
    private final class Segmenter {
        private final Vad vad;
        private final OfflineRecognizer recognizer;
        private final Writer[] out;
        private final float[] window = new float[ModelManager.VAD_WINDOW];
        private int windowFill;
        /** 直近の音声 (発話の頭の補完用)。history[i % 長さ] が通算 i 番目のサンプル */
        private final float[] history = new float[HISTORY_SAMPLES];
        private long total;
        private long prevSegmentEnd;

        Segmenter(Vad vad, OfflineRecognizer recognizer, Writer[] out) {
            this.vad = vad;
            this.recognizer = recognizer;
            this.out = out;
        }

        void accept(short[] chunk) {
            for (short v : chunk) {
                float f = v / 32768f;
                history[(int) (total % HISTORY_SAMPLES)] = f;
                total++;
                window[windowFill++] = f;
                if (windowFill == window.length) {
                    vad.acceptWaveform(window);
                    windowFill = 0;
                    drainSegments();
                }
            }
        }

        void finish() {
            if (windowFill > 0) {
                vad.acceptWaveform(Arrays.copyOf(window, windowFill));
                windowFill = 0;
            }
            vad.flush();
            drainSegments();
        }

        private void drainSegments() {
            while (!vad.empty()) {
                SpeechSegment segment = vad.front();
                vad.pop();
                decode(withLeadingPad(segment));
            }
        }

        /** 発話の手前の音声を最大 0.5 秒足す。直前の発話と重なる部分は足さない。 */
        private float[] withLeadingPad(SpeechSegment segment) {
            float[] samples = segment.getSamples();
            long start = segment.getStart();
            long from = Math.max(Math.max(prevSegmentEnd, start - SEGMENT_PAD_SAMPLES),
                    total - HISTORY_SAMPLES);
            prevSegmentEnd = start + samples.length;
            int pad = (int) Math.max(0, start - from);
            if (pad == 0) {
                return samples;
            }
            float[] padded = new float[pad + samples.length];
            for (int k = 0; k < pad; k++) {
                padded[k] = history[(int) ((from + k) % HISTORY_SAMPLES)];
            }
            System.arraycopy(samples, 0, padded, pad, samples.length);
            return padded;
        }

        private void decode(float[] samples) {
            OfflineStream stream = recognizer.createStream();
            try {
                stream.acceptWaveform(samples, SAMPLE_RATE);
                recognizer.decode(stream);
                appendFinal(recognizer.getResult(stream).getText(), out);
            } finally {
                stream.release();
            }
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

    /**
     * 確定した 1 文を画面用テキストとファイルに追加する。
     * ファイル書き込みに失敗しても認識は止めず、以降の保存だけを諦める (out[0] を null にする)。
     */
    private void appendFinal(String recognized, Writer[] out) {
        String line = TextFormatter.clean(recognized);
        synchronized (LOCK) {
            partial = "";
            if (!line.isEmpty()) {
                text.append(line).append('\n');
            }
        }
        Writer writer = out[0];
        if (!line.isEmpty() && writer != null) {
            try {
                writer.write(line);
                writer.write('\n');
                writer.flush();
            } catch (IOException e) {
                Log.e(TAG, "save failed; continuing in memory", e);
                out[0] = null;
                try {
                    writer.close();
                } catch (IOException ignored) {
                    // 既に書けない状態
                }
                synchronized (LOCK) {
                    saveFailed = true;
                }
            }
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
}
