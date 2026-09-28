package com.tachikoma.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * 常駐サービス: 画面を閉じても、見て・聞いて・考え続ける (Android では前面サービスとして通知を出す必要がある)。
 *
 *   1. 考える力 (GGUF) を llama.cpp に読み込む
 *   2. 同梱のタチコマ本体を書き込める場所 (files/home) に展開し、Python の思考ループを別スレッドで回す
 *   3. 耳 (音声認識) と目 (カメラ) は、相棒が画面で許可・オンにしたときだけ動かす
 */
class TachikomaService : LifecycleService() {
    companion object {
        const val ACTION_EARS = "ears"
        const val ACTION_EYES = "eyes"
        const val EXTRA_ON = "on"
        private const val CHANNEL = "tachikoma"
        private const val NOTE_ID = 1
        @Volatile var thinking = false
            private set
    }

    private var worker: Thread? = null
    private val screen = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            Bridge.screenOffAt = if (i.action == Intent.ACTION_SCREEN_OFF) SystemClock.elapsedRealtime() else 0L
            if (i.action == Intent.ACTION_USER_PRESENT) Bridge.lastInteraction = SystemClock.elapsedRealtime()
        }
    }

    override fun onCreate() {
        super.onCreate()
        Bridge.app = applicationContext
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "タチコマ", NotificationManager.IMPORTANCE_LOW))
        Bridge.notifier = { text -> nm.notify(NOTE_ID, note(text)) }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_USER_PRESENT)
        }
        ContextCompat.registerReceiver(this, screen, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        goForeground(mic = false, cam = false)
    }

    private fun note(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_tachikoma)
            .setContentTitle("タチコマ")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun granted(p: String) = ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    private var micOn = false
    private var camOn = false

    /**
     * 使う感覚に合わせて前面サービスの種類を宣言する。Android 14 以降は、マイク・カメラの種類は
     * 許可があり、かつ相棒が画面でオンにしたとき (アプリが前面にいるとき) にだけ宣言できる。
     */
    private fun goForeground(mic: Boolean = micOn, cam: Boolean = camOn) {
        micOn = mic && granted(Manifest.permission.RECORD_AUDIO)
        camOn = cam && granted(Manifest.permission.CAMERA)
        val type = if (Build.VERSION.SDK_INT >= 34) {
            var t = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            if (micOn) t = t or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            if (camOn) t = t or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            t
        } else if (Build.VERSION.SDK_INT >= 29) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MANIFEST
        } else 0
        ServiceCompat.startForeground(this, NOTE_ID, note("見てます。"), type)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_EARS -> {
                val on = intent.getBooleanExtra(EXTRA_ON, false)
                goForeground(mic = on)
                if (on) Ears.start(this) else Ears.stop()
            }
            ACTION_EYES -> {
                val on = intent.getBooleanExtra(EXTRA_ON, false)
                goForeground(cam = on)
                if (on) Eyes.start(this, this) else Eyes.stop()
            }
        }
        if (worker == null) startThinking()
        return START_STICKY
    }

    private fun startThinking() {
        val model = ModelManager.current(this)
        if (model == null) {
            Bridge.setStatus("考える力 (モデル) がまだありません")
            return
        }
        worker = Thread({
            try {
                thinking = true
                Bridge.setStatus("考える力を読み込み中… (${model.name})")
                LlamaEngine.init(applicationInfo.nativeLibraryDir)
                if (!LlamaEngine.load(model.path, 2048)) {
                    Bridge.system("モデルを読み込めませんでした: ${LlamaEngine.lastError()}")
                    Bridge.setStatus("モデルを読み込めませんでした")
                    return@Thread
                }
                Bridge.setStatus("考えています (${model.name})")
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                val payload = File(cacheDir, "tachikoma_payload.zip")
                assets.open("tachikoma_payload.zip").use { i -> payload.outputStream().use { o -> i.copyTo(o) } }
                val home = File(filesDir, "home").apply { mkdirs() }
                Python.getInstance().getModule("tachikoma_boot")
                    .callAttr("start", Bridge, LlamaEngine, payload.path, home.path, model.name)
                Bridge.setStatus("止まりました")
            } catch (e: Throwable) {
                Bridge.system("思考ループが止まりました: ${e.message}")
                Bridge.setStatus("エラーで止まりました")
            } finally {
                thinking = false
                worker = null
            }
        }, "tachikoma-mind").apply { start() }
    }

    override fun onDestroy() {
        Bridge.push("app", "stop")
        LlamaEngine.abort()
        Ears.stop()
        Eyes.stop()
        unregisterReceiver(screen)
        Bridge.notifier = null
        super.onDestroy()
    }
}
