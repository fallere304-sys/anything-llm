package app.mojiokoshi;

import android.content.Context;
import android.content.res.AssetManager;
import android.util.Log;

import com.k2fsa.sherpa.onnx.OfflineModelConfig;
import com.k2fsa.sherpa.onnx.OfflineRecognizer;
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig;
import com.k2fsa.sherpa.onnx.OfflineTransducerModelConfig;
import com.k2fsa.sherpa.onnx.SileroVadModelConfig;
import com.k2fsa.sherpa.onnx.Vad;
import com.k2fsa.sherpa.onnx.VadModelConfig;

import java.io.IOException;

/**
 * APK に同梱した音声認識モデル (sherpa-onnx + ReazonSpeech) を読み込んで保持する。
 * 読み込み (数秒〜十数秒) はアプリ起動直後にバックグラウンドで始め、
 * 開始ボタンが押された時点で待たずに済むようにする。
 * モデルは assets から直接読むので、ストレージへの展開は不要。
 */
final class ModelManager {
    private static final String TAG = "ModelManager";
    private static final String DIR = "asr/";
    static final int SAMPLE_RATE = 16000;
    /** Silero VAD に一度に渡すサンプル数 */
    static final int VAD_WINDOW = 512;
    /**
     * 認識スレッド数。Xperia Z4 (Snapdragon 810) の高性能コア 4 つのうち 2 つを使う。
     * 4 にすると速くはなるが発熱で速度を落とされやすい。
     */
    private static final int NUM_THREADS = 2;

    private static final Object LOCK = new Object();
    private static Thread loader;
    private static OfflineRecognizer recognizer;
    private static IOException error;

    private ModelManager() {
    }

    /** バックグラウンドでモデルの準備を開始する (多重呼び出し可)。 */
    static void prepareAsync(Context context) {
        final AssetManager assets = context.getApplicationContext().getAssets();
        synchronized (LOCK) {
            if (recognizer != null || loader != null) {
                return;
            }
            error = null;
            loader = new Thread(new Runnable() {
                @Override
                public void run() {
                    OfflineRecognizer loaded = null;
                    IOException failure = null;
                    try {
                        loaded = new OfflineRecognizer(assets, recognizerConfig());
                    } catch (RuntimeException | UnsatisfiedLinkError e) {
                        failure = new IOException(e.toString(), e);
                    }
                    synchronized (LOCK) {
                        recognizer = loaded;
                        error = failure;
                        loader = null;
                        LOCK.notifyAll();
                    }
                    if (failure != null) {
                        Log.e(TAG, "model load failed", failure);
                    }
                }
            }, "asr-model-loader");
            loader.start();
        }
    }

    static boolean isReady() {
        synchronized (LOCK) {
            return recognizer != null;
        }
    }

    /** 認識器の準備完了を待って返す。失敗していれば IOException。 */
    static OfflineRecognizer await(Context context) throws IOException, InterruptedException {
        prepareAsync(context);
        synchronized (LOCK) {
            while (recognizer == null && error == null) {
                LOCK.wait();
            }
            if (recognizer != null) {
                return recognizer;
            }
            IOException e = error;
            // 次回呼び出し時に再試行できるようにしておく
            error = null;
            throw e;
        }
    }

    /** 発話区切り検出器を作る。状態を持つので文字起こしのたびに新しく作る (軽量)。 */
    static Vad createVad(Context context) {
        SileroVadModelConfig silero = new SileroVadModelConfig();
        silero.setModel(DIR + "silero_vad.onnx");
        silero.setThreshold(0.5f);
        // 0.8 秒の無音で 1 発話とみなす (短いと文の途中の間で切れて誤認識が増える)
        silero.setMinSilenceDuration(0.8f);
        silero.setMinSpeechDuration(0.25f);
        silero.setWindowSize(VAD_WINDOW);
        // モデルが扱えるのは 30 秒程度までなので、長い発話は 20 秒で区切る
        silero.setMaxSpeechDuration(20f);

        VadModelConfig config = new VadModelConfig();
        config.setSileroVadModelConfig(silero);
        config.setSampleRate(SAMPLE_RATE);
        config.setNumThreads(1);
        return new Vad(context.getApplicationContext().getAssets(), config);
    }

    private static OfflineRecognizerConfig recognizerConfig() {
        OfflineTransducerModelConfig transducer = new OfflineTransducerModelConfig();
        transducer.setEncoder(DIR + "encoder-epoch-99-avg-1.int8.onnx");
        transducer.setDecoder(DIR + "decoder-epoch-99-avg-1.onnx");
        transducer.setJoiner(DIR + "joiner-epoch-99-avg-1.int8.onnx");

        OfflineModelConfig model = new OfflineModelConfig();
        model.setTransducer(transducer);
        model.setTokens(DIR + "tokens.txt");
        model.setModelType("transducer");
        model.setNumThreads(NUM_THREADS);

        OfflineRecognizerConfig config = new OfflineRecognizerConfig();
        config.setModelConfig(model);
        config.setDecodingMethod("greedy_search");
        return config;
    }
}
