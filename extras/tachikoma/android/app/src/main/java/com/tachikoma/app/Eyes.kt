package com.tachikoma.app

import android.content.Context
import android.os.SystemClock
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import com.google.mlkit.vision.label.ImageLabeling
import com.google.mlkit.vision.label.defaults.ImageLabelerOptions
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.japanese.JapaneseTextRecognizerOptions
import org.json.JSONObject
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * 目: カメラ。画像は保存も送信もしない。端末の中だけで動く ML Kit を使う。
 *   - 1 秒に 1 回、人 (顔) がいるかを見て、現れた / いなくなったを思考ループに渡す
 *   - 頼まれたとき (describeCamera) だけ、映っている物の名前と文字 (日本語の文字認識) を文章にして返す
 *     → 「目の端に映っているもの」も、思考ループの深掘りの材料になる
 */
object Eyes {
    @Volatile var running = false
        private set
    var useBackCamera = false
    private var provider: ProcessCameraProvider? = null
    private val exec = Executors.newSingleThreadExecutor()
    private val faces by lazy {
        FaceDetection.getClient(FaceDetectorOptions.Builder().setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST).build())
    }
    private val labeler by lazy { ImageLabeling.getClient(ImageLabelerOptions.DEFAULT_OPTIONS) }
    private val reader by lazy { TextRecognition.getClient(JapaneseTextRecognizerOptions.Builder().build()) }
    @Volatile private var pending: CompletableFuture<String?>? = null
    private var present = false
    private var lastSeen = 0L
    private var lastCheck = 0L

    fun start(owner: LifecycleOwner, ctx: Context) {
        val future = ProcessCameraProvider.getInstance(ctx)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(exec) { analyze(it) }
                p.unbindAll()
                val selector = if (useBackCamera) CameraSelector.DEFAULT_BACK_CAMERA else CameraSelector.DEFAULT_FRONT_CAMERA
                p.bindToLifecycle(owner, selector, analysis)
                running = true
                Bridge.system("目: カメラを使います (画像は保存も送信もしません)。")
            } catch (e: Exception) {
                running = false
                Bridge.system("カメラを使えません: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(ctx))
    }

    fun stop() {
        running = false
        provider?.unbindAll()
        provider = null
    }

    /** 映っている物の名前と文字を文章で返す。時間内に見られなければ null。 */
    fun describe(timeoutMs: Long): String? {
        if (!running) return null
        val f = CompletableFuture<String?>()
        pending = f
        return try {
            f.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            null
        } finally {
            if (pending === f) pending = null
        }
    }

    @androidx.annotation.OptIn(markerClass = [ExperimentalGetImage::class])
    private fun analyze(proxy: ImageProxy) {
        val want = pending
        try {
            val now = SystemClock.elapsedRealtime()
            if (want == null && now - lastCheck < 1000) return
            lastCheck = now
            val media = proxy.image ?: return
            val input = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
            val found = Tasks.await(faces.process(input), 3, TimeUnit.SECONDS).isNotEmpty()
            if (found) {
                lastSeen = now
                if (!present) {
                    present = true
                    Bridge.push("camera", "person_appeared", "カメラに人が映った", JSONObject().put("present", true))
                }
            } else if (present && now - lastSeen > 60_000) {
                present = false
                Bridge.push("camera", "person_left", "カメラから人がいなくなった", JSONObject().put("present", false))
            }
            if (want != null) {
                val labels = Tasks.await(labeler.process(input), 3, TimeUnit.SECONDS)
                    .filter { it.confidence >= 0.6f }.take(6).joinToString("、") { it.text }
                val text = Tasks.await(reader.process(input), 5, TimeUnit.SECONDS).text
                    .replace('\n', ' ').trim().take(300)
                val parts = mutableListOf<String>()
                if (found) parts.add("人が映っている")
                if (labels.isNotEmpty()) parts.add("物: $labels")
                if (text.isNotEmpty()) parts.add("文字: $text")
                want.complete(parts.joinToString(" / ").ifEmpty { "はっきり見分けられるものは無い" })
            }
        } catch (e: Exception) {
            want?.complete(null)
        } finally {
            proxy.close()
        }
    }
}
