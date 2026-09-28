// タチコマの推論層 (Android / JNI)。llama.cpp で GGUF の小型モデルを端末の CPU で動かす。
//
// Python (思考ループ) → Kotlin (LlamaEngine) → ここ。文字列はすべて UTF-8 のバイト列で受け渡す
// (JNI の「修正 UTF-8」だと絵文字などが壊れるため)。
//
//   nativeInit(libDir)                       CPU 向けの最適な版の計算部を読み込む
//   nativeLoad(path, nCtx, nThreads)         モデルを読み込む
//   nativeChat(system, user, grammar, ...)   1 回の応答を作る。grammar (GBNF) を渡すと出力をその形に縛る
//   nativeSetAdapter(path, scale)            学習した LoRA アダプタ (GGUF) を適用する ("" で外す)
//   nativeAbort()                            背景の推論を打ち切る (相棒が話しかけたとき)
//   nativeCountTokens(text) / nativeInfo() / nativeLastError() / nativeUnload()
//
// 文法つきの生成は、まず普通に 1 トークン選び、文法に合わなければ全語彙に文法を当てて選び直す
// (語彙が 15 万語あるので、毎回全語彙を文法で調べると遅い)。

#include <jni.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "ggml-backend.h"
#include "llama.h"

#ifdef __ANDROID__
#include <android/log.h>
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "tachikoma-llm", __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "tachikoma-llm", __VA_ARGS__)
#else
#include <cstdio>
#define LOGE(...) (fprintf(stderr, __VA_ARGS__), fputc('\n', stderr))
#define LOGI(...) ((void) 0)
#endif

namespace {

std::mutex g_mu;
llama_model * g_model = nullptr;
llama_context * g_ctx = nullptr;
std::map<std::string, llama_adapter_lora *> g_adapters;
std::atomic<bool> g_abort{false};
std::string g_error;

std::string from_bytes(JNIEnv * env, jbyteArray a) {
    if (a == nullptr) {
        return "";
    }
    const jsize n = env->GetArrayLength(a);
    std::string s(static_cast<size_t>(n), '\0');
    if (n > 0) {
        env->GetByteArrayRegion(a, 0, n, reinterpret_cast<jbyte *>(&s[0]));
    }
    return s;
}

jbyteArray to_bytes(JNIEnv * env, const std::string & s) {
    jbyteArray a = env->NewByteArray(static_cast<jsize>(s.size()));
    if (a != nullptr && !s.empty()) {
        env->SetByteArrayRegion(a, 0, static_cast<jsize>(s.size()), reinterpret_cast<const jbyte *>(s.data()));
    }
    return a;
}

void log_cb(ggml_log_level level, const char * text, void *) {
    if (level >= GGML_LOG_LEVEL_ERROR) {
        LOGE("%s", text);
    }
}

void unload_locked() {
    g_adapters.clear();          // アダプタはモデルと一緒に解放される
    if (g_ctx != nullptr) {
        llama_free(g_ctx);
        g_ctx = nullptr;
    }
    if (g_model != nullptr) {
        llama_model_free(g_model);
        g_model = nullptr;
    }
}

std::vector<llama_token> tokenize(const std::string & text, bool add_special) {
    const llama_vocab * vocab = llama_model_get_vocab(g_model);
    const int n = -llama_tokenize(vocab, text.data(), static_cast<int32_t>(text.size()), nullptr, 0, add_special, true);
    std::vector<llama_token> toks(static_cast<size_t>(std::max(n, 0)));
    if (n > 0 && llama_tokenize(vocab, text.data(), static_cast<int32_t>(text.size()), toks.data(), n, add_special,
                                true) < 0) {
        toks.clear();
    }
    return toks;
}

// モデルに埋め込まれた会話の型 (chat template) で system + user を 1 本の入力にする
std::string format_chat(const std::string & system, const std::string & user) {
    const char * tmpl = llama_model_chat_template(g_model, nullptr);
    const llama_chat_message msgs[2] = {{"system", system.c_str()}, {"user", user.c_str()}};
    std::vector<char> buf(2 * (system.size() + user.size()) + 1024);
    for (const char * t : {tmpl, "chatml"}) {
        if (t == nullptr) {
            continue;
        }
        int n = llama_chat_apply_template(t, msgs, 2, true, buf.data(), static_cast<int32_t>(buf.size()));
        if (n > static_cast<int>(buf.size())) {
            buf.resize(static_cast<size_t>(n) + 1);
            n = llama_chat_apply_template(t, msgs, 2, true, buf.data(), static_cast<int32_t>(buf.size()));
        }
        if (n > 0) {
            return std::string(buf.data(), static_cast<size_t>(n));
        }
    }
    return system + "\n\n" + user + "\n";
}

bool decode_prompt(const std::vector<llama_token> & toks, bool allow_abort) {
    const int n_batch = static_cast<int>(llama_n_batch(g_ctx));
    for (size_t i = 0; i < toks.size(); i += static_cast<size_t>(n_batch)) {
        if (allow_abort && g_abort.load()) {
            g_error = "aborted";
            return false;
        }
        const int n = static_cast<int>(std::min(toks.size() - i, static_cast<size_t>(n_batch)));
        llama_batch b = llama_batch_get_one(const_cast<llama_token *>(toks.data() + i), n);
        if (llama_decode(g_ctx, b) != 0) {
            g_error = "llama_decode failed (prompt)";
            return false;
        }
    }
    return true;
}

}  // namespace

extern "C" {

JNIEXPORT void JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeInit(JNIEnv * env, jobject, jbyteArray jlib_dir) {
    llama_log_set(log_cb, nullptr);
    const std::string dir = from_bytes(env, jlib_dir);
    if (!dir.empty()) {
        ggml_backend_load_all_from_path(dir.c_str());
    }
    llama_backend_init();
}

JNIEXPORT jboolean JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeLoad(JNIEnv * env, jobject, jbyteArray jpath, jint n_ctx, jint n_threads) {
    std::lock_guard<std::mutex> lock(g_mu);
    unload_locked();
    g_error.clear();
    const std::string path = from_bytes(env, jpath);
    llama_model_params mp = llama_model_default_params();
    g_model = llama_model_load_from_file(path.c_str(), mp);
    if (g_model == nullptr) {
        g_error = "model load failed: " + path;
        return JNI_FALSE;
    }
    const int hw = static_cast<int>(std::thread::hardware_concurrency());
    const int threads = n_threads > 0 ? n_threads : std::max(1, std::min(4, hw - 2));
    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = static_cast<uint32_t>(n_ctx);
    cp.n_batch = static_cast<uint32_t>(n_ctx);      // 入力全体を 1 回の decode に渡せるように
    cp.n_ubatch = 512;
    cp.n_threads = threads;
    cp.n_threads_batch = threads;
    g_ctx = llama_init_from_model(g_model, cp);
    if (g_ctx == nullptr) {
        llama_model_free(g_model);
        g_model = nullptr;
        g_error = "context init failed";
        return JNI_FALSE;
    }
    LOGI("model loaded: %s (n_ctx=%d, threads=%d)", path.c_str(), n_ctx, threads);
    return JNI_TRUE;
}

JNIEXPORT void JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeUnload(JNIEnv *, jobject) {
    std::lock_guard<std::mutex> lock(g_mu);
    unload_locked();
}

JNIEXPORT void JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeAbort(JNIEnv *, jobject) {
    g_abort.store(true);
}

JNIEXPORT jboolean JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeSetAdapter(JNIEnv * env, jobject, jbyteArray jpath, jfloat scale) {
    std::lock_guard<std::mutex> lock(g_mu);
    g_error.clear();
    if (g_ctx == nullptr) {
        g_error = "model not loaded";
        return JNI_FALSE;
    }
    const std::string path = from_bytes(env, jpath);
    if (path.empty()) {
        return llama_set_adapters_lora(g_ctx, nullptr, 0, nullptr) == 0 ? JNI_TRUE : JNI_FALSE;
    }
    llama_adapter_lora * ad = nullptr;
    auto it = g_adapters.find(path);
    if (it == g_adapters.end()) {
        ad = llama_adapter_lora_init(g_model, path.c_str());
        if (ad == nullptr) {
            g_error = "adapter load failed: " + path;
            return JNI_FALSE;
        }
        g_adapters[path] = ad;
    } else {
        ad = it->second;
    }
    float s = scale;
    if (llama_set_adapters_lora(g_ctx, &ad, 1, &s) != 0) {
        g_error = "adapter apply failed";
        return JNI_FALSE;
    }
    return JNI_TRUE;
}

JNIEXPORT jint JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeCountTokens(JNIEnv * env, jobject, jbyteArray jtext) {
    std::lock_guard<std::mutex> lock(g_mu);
    if (g_model == nullptr) {
        return -1;
    }
    return static_cast<jint>(tokenize(from_bytes(env, jtext), false).size());
}

JNIEXPORT jbyteArray JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeChat(JNIEnv * env, jobject, jbyteArray jsystem, jbyteArray juser,
                                             jbyteArray jgrammar, jint max_tokens, jfloat temperature,
                                             jboolean allow_abort) {
    std::lock_guard<std::mutex> lock(g_mu);
    g_error.clear();
    g_abort.store(false);
    if (g_ctx == nullptr) {
        g_error = "model not loaded";
        return nullptr;
    }
    const llama_vocab * vocab = llama_model_get_vocab(g_model);
    const std::string grammar = from_bytes(env, jgrammar);
    const std::vector<llama_token> toks = tokenize(format_chat(from_bytes(env, jsystem), from_bytes(env, juser)), true);
    const int n_ctx = static_cast<int>(llama_n_ctx(g_ctx));
    if (toks.empty()) {
        g_error = "empty prompt";
        return nullptr;
    }
    if (static_cast<int>(toks.size()) + max_tokens > n_ctx) {
        g_error = "prompt too long: " + std::to_string(toks.size()) + " tokens";
        return nullptr;
    }
    llama_memory_clear(llama_get_memory(g_ctx), true);
    if (!decode_prompt(toks, allow_abort)) {
        return nullptr;
    }

    llama_sampler * chain = llama_sampler_chain_init(llama_sampler_chain_default_params());
    if (temperature <= 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(chain, llama_sampler_init_top_k(40));
        llama_sampler_chain_add(chain, llama_sampler_init_top_p(0.95f, 1));
        llama_sampler_chain_add(chain, llama_sampler_init_temp(temperature));
        llama_sampler_chain_add(chain, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }
    llama_sampler * gram = nullptr;
    if (!grammar.empty()) {
        gram = llama_sampler_init_grammar(vocab, grammar.c_str(), "root");
        if (gram == nullptr) {
            llama_sampler_free(chain);
            g_error = "grammar parse failed";
            return nullptr;
        }
    }

    const int n_vocab = llama_vocab_n_tokens(vocab);
    std::vector<llama_token_data> cur;
    std::string out;
    bool ok = true;
    for (int i = 0; i < max_tokens; i++) {
        if (allow_abort && g_abort.load()) {
            g_error = "aborted";
            ok = false;
            break;
        }
        llama_token tok = llama_sampler_sample(chain, g_ctx, -1);
        if (gram != nullptr) {
            llama_token_data single = {tok, 1.0f, 0.0f};
            llama_token_data_array one = {&single, 1, -1, false};
            llama_sampler_apply(gram, &one);
            if (std::isinf(single.logit) && single.logit < 0) {
                // 文法に合わない: 全語彙に文法を当ててから選び直す
                const float * logits = llama_get_logits_ith(g_ctx, -1);
                cur.resize(static_cast<size_t>(n_vocab));
                for (llama_token t = 0; t < n_vocab; t++) {
                    cur[static_cast<size_t>(t)] = {t, logits[t], 0.0f};
                }
                llama_token_data_array arr = {cur.data(), cur.size(), -1, false};
                llama_sampler_apply(gram, &arr);
                llama_sampler_apply(chain, &arr);
                if (arr.selected < 0 || arr.selected >= static_cast<int64_t>(arr.size) ||
                    std::isinf(arr.data[arr.selected].logit)) {
                    g_error = "grammar: no valid token";
                    ok = false;
                    break;
                }
                tok = arr.data[arr.selected].id;
            }
            llama_sampler_accept(gram, tok);
        }
        if (llama_vocab_is_eog(vocab, tok)) {
            break;
        }
        char piece[256];
        const int n = llama_token_to_piece(vocab, tok, piece, sizeof(piece), 0, false);
        if (n > 0) {
            out.append(piece, static_cast<size_t>(n));
        }
        llama_batch b = llama_batch_get_one(&tok, 1);
        if (llama_decode(g_ctx, b) != 0) {
            g_error = "llama_decode failed (generation)";
            ok = false;
            break;
        }
    }
    llama_sampler_free(chain);
    if (gram != nullptr) {
        llama_sampler_free(gram);
    }
    return ok ? to_bytes(env, out) : nullptr;
}

JNIEXPORT jbyteArray JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeLastError(JNIEnv * env, jobject) {
    return to_bytes(env, g_error);
}

JNIEXPORT jbyteArray JNICALL
Java_com_tachikoma_app_LlamaEngine_nativeInfo(JNIEnv * env, jobject) {
    std::lock_guard<std::mutex> lock(g_mu);
    std::string s;
    if (g_model != nullptr) {
        char desc[256];
        llama_model_desc(g_model, desc, sizeof(desc));
        s += desc;
        s += " / " + std::to_string(llama_model_size(g_model) >> 20) + " MiB";
        s += " / " + std::to_string(llama_model_n_params(g_model) / 1000000) + "M params";
        s += " / n_ctx " + std::to_string(g_ctx != nullptr ? llama_n_ctx(g_ctx) : 0);
        s += "\n";
    }
    s += llama_print_system_info();
    return to_bytes(env, s);
}

}  // extern "C"
