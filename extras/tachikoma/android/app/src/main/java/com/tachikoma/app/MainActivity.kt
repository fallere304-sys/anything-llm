package com.tachikoma.app

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.OpenableColumns
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import java.io.File

/**
 * 画面: 文字だけのチャット。タチコマの発話・相棒の入力・(任意で) 思考ログを並べる。
 * 考える力のモデルが無ければ、最初に取得する (約 1GB / 軽い版は約 0.4GB)。
 */
class MainActivity : ComponentActivity() {
    private lateinit var list: LinearLayout
    private lateinit var scroll: ScrollView
    private lateinit var status: TextView
    private lateinit var input: EditText
    private lateinit var micButton: Button
    private lateinit var camButton: Button
    private var showLog = false
    private var pendingExport: String? = null

    private val askPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val askMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) setSense(TachikomaService.ACTION_EARS, true) else Bridge.system("マイクの許可がないので、声は聞けません。")
    }
    private val askCam = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) setSense(TachikomaService.ACTION_EYES, true) else Bridge.system("カメラの許可がないので、目は使えません。")
    }
    private val saveExport = registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val src = pendingExport ?: return@registerForActivityResult
        if (uri != null) copyTo(File(src), uri)
    }
    private val openAdapter = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) importAdapter(uri)
    }
    private val openModel = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) importModel(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Bridge.app = applicationContext
        buildUi()
        if (Build.VERSION.SDK_INT >= 33) askPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        for (line in Bridge.history()) addLine(line)
        debugHooks()
        if (ModelManager.current(this) == null) chooseModel(first = true) else startMind()
    }

    override fun onStart() {
        super.onStart()
        Bridge.onLine = { addLine(it) }
        Bridge.onStatus = { status.text = it }
        status.text = Bridge.status
    }

    override fun onStop() {
        Bridge.onLine = null
        Bridge.onStatus = null
        super.onStop()
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        Bridge.lastInteraction = SystemClock.elapsedRealtime()
        return super.dispatchTouchEvent(ev)
    }

    /** デバッグ版だけ: 自動試験 (CI のエミュレータ) 用に、モデルの選択と最初の話しかけを外から渡せる。 */
    private fun debugHooks() {
        if ((applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) return
        intent.getStringExtra("debug_model")?.let { name ->
            val f = File(ModelManager.dir(this), name)
            if (f.isFile) ModelManager.select(this, f)
        }
        intent.getStringExtra("debug_say")?.let { Bridge.userSays(it) }
    }

    // ------------------------------------------------------------------ 画面
    private fun buildUi() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        status = TextView(this).apply { textSize = 12f; setTextColor(Color.GRAY) }
        root.addView(status)
        val bar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        micButton = Button(this).apply { text = "耳: オフ"; setOnClickListener { toggleMic() } }
        camButton = Button(this).apply { text = "目: オフ"; setOnClickListener { toggleCam() } }
        val logButton = Button(this).apply {
            text = "思考ログ"
            setOnClickListener { showLog = !showLog; redraw() }
        }
        val menu = Button(this).apply { text = "…"; setOnClickListener { showMenu(it) } }
        for (b in listOf(micButton, camButton, logButton, menu)) bar.addView(b)
        root.addView(bar)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroll = ScrollView(this).apply { addView(list) }
        root.addView(scroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        input = EditText(this).apply {
            hint = "話しかける (「/」でコマンド)"
            imeOptions = EditorInfo.IME_ACTION_SEND
            setSingleLine(true)
            setOnEditorActionListener { _, _, _ -> send(); true }
        }
        row.addView(input, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row.addView(Button(this).apply { text = "送る"; setOnClickListener { send() } })
        root.addView(row)
        setContentView(root)
    }

    private fun send() {
        val t = input.text.toString().trim()
        if (t.isEmpty()) return
        input.setText("")
        if (!TachikomaService.thinking) Bridge.system("まだ起動中です。少し待ってね。")
        Bridge.userSays(t)
    }

    private fun addLine(line: ChatLine) {
        val v: View = when (line.kind) {
            ChatLine.Kind.FILE -> Button(this).apply {
                text = "${line.text}を保存する"
                setOnClickListener {
                    pendingExport = line.path
                    saveExport.launch(File(line.path ?: "export.zip").name)
                }
            }
            else -> TextView(this).apply {
                text = when (line.kind) {
                    ChatLine.Kind.TACHIKOMA -> "タチコマ: ${line.text}"
                    ChatLine.Kind.USER -> line.text
                    else -> line.text
                }
                setTextIsSelectable(true)
                setPadding(8, 10, 8, 10)
                when (line.kind) {
                    ChatLine.Kind.USER -> { gravity = Gravity.END; setTextColor(Color.rgb(20, 90, 160)) }
                    ChatLine.Kind.LOG -> { textSize = 11f; setTextColor(Color.GRAY); visibility = if (showLog) View.VISIBLE else View.GONE }
                    ChatLine.Kind.SYSTEM -> { textSize = 12f; setTextColor(Color.DKGRAY); setTypeface(typeface, Typeface.ITALIC) }
                    else -> textSize = 16f
                }
            }
        }
        v.tag = line.kind
        list.addView(v)
        if (list.childCount > 400) list.removeViewAt(0)
        scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun redraw() {
        for (i in 0 until list.childCount) {
            val v = list.getChildAt(i)
            if (v.tag == ChatLine.Kind.LOG) v.visibility = if (showLog) View.VISIBLE else View.GONE
        }
    }

    // ------------------------------------------------------------------ 起動と感覚
    private fun startMind() {
        ContextCompat.startForegroundService(this, Intent(this, TachikomaService::class.java))
    }

    private fun setSense(action: String, on: Boolean) {
        ContextCompat.startForegroundService(this, Intent(this, TachikomaService::class.java)
            .setAction(action).putExtra(TachikomaService.EXTRA_ON, on))
        if (action == TachikomaService.ACTION_EARS) micButton.text = if (on) "耳: オン" else "耳: オフ"
        else camButton.text = if (on) "目: オン" else "目: オフ"
    }

    private fun granted(p: String) = ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    private fun toggleMic() {
        if (Ears.running) setSense(TachikomaService.ACTION_EARS, false)
        else if (granted(Manifest.permission.RECORD_AUDIO)) setSense(TachikomaService.ACTION_EARS, true)
        else askMic.launch(Manifest.permission.RECORD_AUDIO)
    }

    private fun toggleCam() {
        if (Eyes.running) setSense(TachikomaService.ACTION_EYES, false)
        else if (granted(Manifest.permission.CAMERA)) setSense(TachikomaService.ACTION_EYES, true)
        else askCam.launch(Manifest.permission.CAMERA)
    }

    private fun showMenu(anchor: View) {
        val m = PopupMenu(this, anchor)
        m.menu.add(0, 1, 0, "学習データを書き出す (PC で学習する)")
        m.menu.add(0, 2, 0, "学習したアダプタを取り込む")
        m.menu.add(0, 3, 0, "考える力 (モデル) を変える")
        m.menu.add(0, 4, 0, if (Eyes.useBackCamera) "目: 前のカメラにする" else "目: 後ろのカメラにする")
        m.menu.add(0, 5, 0, "推論の情報")
        m.setOnMenuItemClickListener {
            when (it.itemId) {
                1 -> Bridge.push("app", "export")
                2 -> openAdapter.launch(arrayOf("*/*"))
                3 -> chooseModel(first = false)
                4 -> {
                    Eyes.useBackCamera = !Eyes.useBackCamera
                    if (Eyes.running) { setSense(TachikomaService.ACTION_EYES, false); setSense(TachikomaService.ACTION_EYES, true) }
                }
                5 -> Bridge.system(if (LlamaEngine.loadedPath != null) LlamaEngine.info() else "まだ読み込んでいません")
            }
            true
        }
        m.show()
    }

    // ------------------------------------------------------------------ モデル
    private fun chooseModel(first: Boolean) {
        val labels = ModelManager.choices.map { it.label } + "手元の GGUF ファイルを選ぶ"
        AlertDialog.Builder(this)
            .setTitle(if (first) "考える力 (モデル) を取得します" else "考える力 (モデル) を変える")
            .setItems(labels.toTypedArray()) { _, which ->
                if (which < ModelManager.choices.size) download(ModelManager.choices[which])
                else openModel.launch(arrayOf("*/*"))
            }
            .setCancelable(!first)
            .show()
    }

    private fun download(choice: ModelManager.Choice) {
        Bridge.system("${choice.label} を取得します。Wi-Fi をおすすめします。")
        Thread {
            try {
                val f = ModelManager.download(this, choice) { done, total ->
                    Bridge.setStatus("取得中 ${done shr 20} / ${total shr 20} MB")
                }
                Bridge.system("取得しました: ${f.name}")
                runOnUiThread { restartMind() }
            } catch (e: Exception) {
                Bridge.system("取得できませんでした: ${e.message}。手元の GGUF を選ぶこともできます。")
                Bridge.setStatus("モデルがありません")
            }
        }.start()
    }

    private fun restartMind() {
        stopService(Intent(this, TachikomaService::class.java))
        list.postDelayed({ startMind() }, 1500)
    }

    private fun displayName(uri: Uri): String =
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        } ?: "file.gguf"

    private fun importModel(uri: Uri) {
        val name = displayName(uri)
        Thread {
            try {
                contentResolver.openInputStream(uri)?.let { ModelManager.importFrom(this, it, name) }
                Bridge.system("モデルを取り込みました: $name")
                runOnUiThread { restartMind() }
            } catch (e: Exception) {
                Bridge.system("取り込めませんでした: ${e.message}")
            }
        }.start()
    }

    private fun importAdapter(uri: Uri) {
        Thread {
            try {
                val dir = File(filesDir, "home/incoming").apply { mkdirs() }
                val dst = File(dir, "adapter-${System.currentTimeMillis()}.gguf")
                contentResolver.openInputStream(uri)?.use { i -> dst.outputStream().use { o -> i.copyTo(o) } }
                Bridge.push("app", "import_adapter", "", org.json.JSONObject().put("path", dst.path))
            } catch (e: Exception) {
                Bridge.system("取り込めませんでした: ${e.message}")
            }
        }.start()
    }

    private fun copyTo(src: File, uri: Uri) {
        try {
            contentResolver.openOutputStream(uri)?.use { o -> src.inputStream().use { i -> i.copyTo(o) } }
            Bridge.system("保存しました。")
        } catch (e: Exception) {
            Bridge.system("保存できませんでした: ${e.message}")
        }
    }
}
