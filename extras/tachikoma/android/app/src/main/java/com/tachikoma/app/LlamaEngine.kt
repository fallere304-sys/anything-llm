package com.tachikoma.app

/**
 * 推論の窓口 (llama.cpp / JNI: tachikoma_llm.cpp)。Python の思考ループ (tachikoma/android/llm.py) から呼ばれる。
 * 文字列は UTF-8 のバイト列で受け渡す (JNI の修正 UTF-8 だと絵文字などが壊れるため)。
 */
object LlamaEngine {
    init {
        System.loadLibrary("tachikoma_llm")
    }

    private external fun nativeInit(libDir: ByteArray)
    private external fun nativeLoad(path: ByteArray, nCtx: Int, nThreads: Int): Boolean
    private external fun nativeUnload()
    private external fun nativeAbort()
    private external fun nativeSetAdapter(path: ByteArray, scale: Float): Boolean
    private external fun nativeCountTokens(text: ByteArray): Int
    private external fun nativeChat(
        system: ByteArray, user: ByteArray, grammar: ByteArray,
        maxTokens: Int, temperature: Float, allowAbort: Boolean,
    ): ByteArray?
    private external fun nativeLastError(): ByteArray
    private external fun nativeInfo(): ByteArray

    @Volatile
    var loadedPath: String? = null
        private set
    private var initialized = false

    @Synchronized
    fun init(nativeLibDir: String) {
        if (!initialized) {
            nativeInit(nativeLibDir.toByteArray())
            initialized = true
        }
    }

    @Synchronized
    fun load(path: String, nCtx: Int, nThreads: Int = 0): Boolean {
        val ok = nativeLoad(path.toByteArray(), nCtx, nThreads)
        loadedPath = if (ok) path else null
        return ok
    }

    @Synchronized
    fun unload() {
        nativeUnload()
        loadedPath = null
    }

    /** 背景の推論 (allowAbort=true のもの) を打ち切る。相棒が話しかけたときに呼ぶ。 */
    fun abort() = nativeAbort()

    fun setAdapter(path: String, scale: Float): Boolean = nativeSetAdapter(path.toByteArray(), scale)

    fun countTokens(text: String): Int = nativeCountTokens(text.toByteArray())

    fun chat(system: String, user: String, grammar: String, maxTokens: Int, temperature: Float, allowAbort: Boolean): String? =
        nativeChat(system.toByteArray(), user.toByteArray(), grammar.toByteArray(), maxTokens, temperature, allowAbort)
            ?.toString(Charsets.UTF_8)

    fun lastError(): String = nativeLastError().toString(Charsets.UTF_8)

    fun info(): String = nativeInfo().toString(Charsets.UTF_8)
}
