package com.tachikoma.app

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentLinkedQueue

/** 画面に出す 1 行。出力は文字だけ。 */
data class ChatLine(val kind: Kind, val text: String, val path: String? = null) {
    enum class Kind { TACHIKOMA, USER, LOG, FILE, SYSTEM }
}

/**
 * Python の思考ループ (tachikoma/android/device.py) と、端末の入出力をつなぐ窓口。
 * 入力 (文字・声・カメラ・操作) は push() で溜め、思考ループが poll() でまとめて受け取る。
 * 出力 (say / log) は画面と通知へ。どちらの方向も、ここ以外を通らない。
 */
object Bridge {
    lateinit var app: Context
    private val inbox = ConcurrentLinkedQueue<JSONObject>()
    private val main = Handler(Looper.getMainLooper())
    private val lines = ArrayList<ChatLine>()
    var onLine: ((ChatLine) -> Unit)? = null
    var onStatus: ((String) -> Unit)? = null
    var notifier: ((String) -> Unit)? = null
    @Volatile var status: String = ""
        private set
    @Volatile var screenOffAt: Long = 0L         // 0 = 画面がついている
    @Volatile var lastInteraction: Long = SystemClock.elapsedRealtime()

    fun history(): List<ChatLine> = synchronized(lines) { ArrayList(lines) }

    private fun add(line: ChatLine) {
        synchronized(lines) {
            lines.add(line)
            if (lines.size > 400) lines.subList(0, lines.size - 400).clear()
        }
        main.post { onLine?.invoke(line) }
    }

    fun setStatus(text: String) {
        status = text
        main.post { onStatus?.invoke(text) }
    }

    fun system(text: String) {
        Log.i("tachikoma", "SYSTEM: $text")
        add(ChatLine(ChatLine.Kind.SYSTEM, text))
    }

    // ------------------------------------------------------------------ 入力
    fun push(source: String, kind: String, text: String = "", meta: JSONObject? = null) {
        val o = JSONObject().put("source", source).put("kind", kind).put("text", text)
        if (meta != null) o.put("meta", meta)
        inbox.add(o)
        if (source == "user" || source == "voice") {
            lastInteraction = SystemClock.elapsedRealtime()
            LlamaEngine.abort()          // 相棒が話しかけた: 背景の推論をやめて返事を優先
        }
    }

    fun userSays(text: String) {
        Log.i("tachikoma", "USER: $text")
        add(ChatLine(ChatLine.Kind.USER, text))
        push("user", "user_message", text)
    }

    // ------------------------------------------------------------------ Python から呼ばれる
    fun poll(): String? {
        if (inbox.isEmpty()) return null
        val arr = JSONArray()
        while (true) arr.put(inbox.poll() ?: break)
        return arr.toString()
    }

    fun say(text: String) {
        val t = text.replace(Regex("^\\[タチコマ [0-9:]+\\] "), "")
        Log.i("tachikoma", "TACHIKOMA: $t")
        add(ChatLine(ChatLine.Kind.TACHIKOMA, t))
        notifier?.invoke(t)
    }

    fun log(text: String) {
        Log.d("tachikoma", "LOG: $text")
        add(ChatLine(ChatLine.Kind.LOG, text))
    }

    fun state(json: String) = setStatus(json)

    fun idleSeconds(): Double {
        val now = SystemClock.elapsedRealtime()
        val off = screenOffAt
        return if (off > 0) (now - off) / 1000.0 else (now - lastInteraction) / 1000.0
    }

    private fun batteryIntent(): Intent? = app.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))

    /** 背景思考をしてよいか: 充電中か、電池が minPercent 以上。 */
    fun batteryOk(minPercent: Int): Boolean {
        val i = batteryIntent() ?: return true
        val status = i.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        if (status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL) return true
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        return level < 0 || level * 100 / scale.coerceAtLeast(1) >= minPercent
    }

    /** 電池の放電の速さ (W)。測れない端末では 0。 */
    fun batteryWatts(): Double {
        val bm = app.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val microAmps = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW)
        val milliVolts = batteryIntent()?.getIntExtra(BatteryManager.EXTRA_VOLTAGE, 0) ?: 0
        if (microAmps == Int.MIN_VALUE || microAmps == 0 || milliVolts <= 0) return 0.0
        return Math.abs(microAmps) / 1e6 * milliVolts / 1e3
    }

    fun cameraOn(): Boolean = Eyes.running

    fun describeCamera(timeoutMs: Int): String? = Eyes.describe(timeoutMs.toLong())

    fun offerFile(path: String, title: String) = add(ChatLine(ChatLine.Kind.FILE, title, path))
}
