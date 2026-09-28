package com.tachikoma.app

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL

/**
 * 考える力 (GGUF の小型モデル) の置き場所と取得。RAM 4GB の端末を想定して、1GB 前後以下のものを選ぶ。
 *
 *   standard  TinySwallow-1.5B-Instruct (Sakana AI、Qwen2.5 を日本語で鍛えた 1.5B)。公式の GGUF の Q5_K_M 約 1.1GB
 *   light     Qwen2.5-0.5B-Instruct Q4_K_M 約 0.4GB (もっと軽く・速く。日本語の質は下がる)
 *
 * ファイル名は Hugging Face の API で一覧を取って選ぶ (量子化の種類の優先順位つき)。
 * 取れなければ、手元の GGUF を選んで取り込むこともできる。
 */
object ModelManager {
    data class Choice(val key: String, val label: String, val repos: List<String>)

    val choices = listOf(
        Choice("standard", "日本語に強い 1.5B (TinySwallow・約 1.1GB)",
            listOf("SakanaAI/TinySwallow-1.5B-Instruct-GGUF", "Qwen/Qwen2.5-1.5B-Instruct-GGUF")),
        Choice("light", "もっと軽い 0.5B (Qwen2.5・約 0.4GB)", listOf("Qwen/Qwen2.5-0.5B-Instruct-GGUF")),
    )
    private val preferred = listOf("q4_k_m", "q4_0", "q5_k_m", "q8_0")

    fun dir(ctx: Context): File = File(ctx.filesDir, "models").apply { mkdirs() }

    fun current(ctx: Context): File? {
        val name = ctx.getSharedPreferences("tachikoma", Context.MODE_PRIVATE).getString("model", null) ?: return null
        return File(dir(ctx), name).takeIf { it.isFile && it.length() > 1_000_000 }
    }

    fun select(ctx: Context, file: File) {
        ctx.getSharedPreferences("tachikoma", Context.MODE_PRIVATE).edit().putString("model", file.name).apply()
    }

    private fun get(url: String): String {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 20000
        c.readTimeout = 30000
        c.setRequestProperty("User-Agent", "Tachikoma-Android")
        return c.inputStream.bufferedReader().use { it.readText() }
    }

    /** repo の中から、優先順位の高い量子化の GGUF のファイル名を選ぶ。 */
    fun pickFile(siblings: List<String>): String? {
        val ggufs = siblings.filter { it.endsWith(".gguf", true) && !it.contains("-of-") && !it.contains("mmproj", true) }
        for (p in preferred) ggufs.firstOrNull { it.lowercase().contains(p) }?.let { return it }
        return ggufs.firstOrNull()
    }

    /** 取得する (途中からの再開つき)。onProgress(取得済み, 全体) を呼ぶ。成功したらファイルを返す。 */
    fun download(ctx: Context, choice: Choice, onProgress: (Long, Long) -> Unit): File {
        var last: Exception? = null
        for (repo in choice.repos) {
            try {
                val info = JSONObject(get("https://huggingface.co/api/models/$repo"))
                val sib = info.getJSONArray("siblings")
                val names = (0 until sib.length()).map { sib.getJSONObject(it).getString("rfilename") }
                val name = pickFile(names) ?: continue
                val dst = File(dir(ctx), name.substringAfterLast('/'))
                fetch("https://huggingface.co/$repo/resolve/main/$name", dst, onProgress)
                select(ctx, dst)
                return dst
            } catch (e: Exception) {
                last = e
            }
        }
        throw last ?: IllegalStateException("モデルが見つかりませんでした")
    }

    private fun fetch(url: String, dst: File, onProgress: (Long, Long) -> Unit) {
        if (dst.isFile && dst.length() > 1_000_000) return
        val part = File(dst.path + ".part")
        val have = if (part.exists()) part.length() else 0L
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 20000
        c.readTimeout = 60000
        c.instanceFollowRedirects = true
        c.setRequestProperty("User-Agent", "Tachikoma-Android")
        if (have > 0) c.setRequestProperty("Range", "bytes=$have-")
        val resumed = c.responseCode == 206
        val total = (if (resumed) have else 0L) + c.contentLengthLong
        RandomAccessFile(part, "rw").use { out ->
            if (!resumed) out.setLength(0) else out.seek(have)
            var done = if (resumed) have else 0L
            val buf = ByteArray(1 shl 16)
            c.inputStream.use { input ->
                var lastReport = 0L
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    done += n
                    if (done - lastReport > (4 shl 20)) {
                        onProgress(done, total)
                        lastReport = done
                    }
                }
            }
            onProgress(done, total)
        }
        if (!part.renameTo(dst)) throw IllegalStateException("保存に失敗しました")
    }

    /** 相棒が選んだ GGUF を取り込む。 */
    fun importFrom(ctx: Context, input: java.io.InputStream, name: String): File {
        val safe = name.substringAfterLast('/').ifBlank { "model.gguf" }.let { if (it.endsWith(".gguf", true)) it else "$it.gguf" }
        val dst = File(dir(ctx), safe)
        input.use { i -> dst.outputStream().use { o -> i.copyTo(o) } }
        select(ctx, dst)
        return dst
    }
}
