package com.tachikoma.app

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import org.json.JSONObject
import kotlin.math.ln

/**
 * 耳: 端末の音声認識で聞き続け、聞き取った文を思考ループに渡す (source=voice, kind=speech)。
 * 端末の中だけで認識できる場合はそれを使う (Android 12 以降)。自分に話しかけられたかどうかは、
 * PC 版と同じく思考側が呼びかけ語 (「タチコマ」) で判定する。聞き取りの自信が低ければ聞き返す。
 */
object Ears : RecognitionListener {
    private val main = Handler(Looper.getMainLooper())
    private var sr: SpeechRecognizer? = null
    private var ctx: Context? = null
    @Volatile var running = false
        private set
    var onDevice = false
        private set

    fun start(context: Context) = main.post {
        if (running) return@post
        ctx = context.applicationContext
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            Bridge.system("この端末では音声認識が使えません。")
            return@post
        }
        onDevice = Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        sr = if (onDevice) SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        else SpeechRecognizer.createSpeechRecognizer(context)
        sr?.setRecognitionListener(this)
        running = true
        Bridge.system(if (onDevice) "耳: 端末の中で聞き取ります。" else "耳: 端末の標準の音声認識で聞き取ります (ネット経由の場合があります)。")
        listen()
    }

    fun stop() = main.post {
        running = false
        sr?.destroy()
        sr = null
    }

    private fun listen() {
        if (!running) return
        val i = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ja-JP")
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        try {
            sr?.startListening(i)
        } catch (e: Exception) {
            again(2000)
        }
    }

    private fun again(delayMs: Long) = main.postDelayed({ listen() }, delayMs)

    override fun onResults(results: Bundle) {
        val texts = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
        val conf = results.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES)
        val text = texts?.firstOrNull()?.trim().orEmpty()
        if (text.isNotEmpty()) {
            // 自信 (0-1) を PC 版の Whisper と同じ尺度 (平均対数確率) にそろえる。わからなければ 0 (= 自信あり扱い)
            val c = conf?.firstOrNull() ?: -1f
            val logprob = if (c > 0f) ln(c.toDouble()) else 0.0
            Bridge.push("voice", "speech", text, JSONObject().put("avg_logprob", logprob))
        }
        again(250)
    }

    override fun onError(error: Int) {
        when (error) {
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> {
                Bridge.system("マイクの許可がありません。")
                running = false
            }
            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> again(250)
            else -> again(2000)
        }
    }

    override fun onReadyForSpeech(params: Bundle?) {}
    override fun onBeginningOfSpeech() {}
    override fun onRmsChanged(rmsdB: Float) {}
    override fun onBufferReceived(buffer: ByteArray?) {}
    override fun onEndOfSpeech() {}
    override fun onPartialResults(partialResults: Bundle?) {}
    override fun onEvent(eventType: Int, params: Bundle?) {}
}
