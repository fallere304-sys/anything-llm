plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// タチコマ本体 (PC 版と同じ Python のソース) を APK の assets に同梱する。
// 端末では初回起動時に書き込める場所へ展開し、自己進化はそこを書き換える (tachikoma_boot.py)
val tachikomaRoot: File = rootDir.parentFile
val pythonExe: String = (findProperty("tachikoma.python") as String?)
    ?: if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3"
val payloadDir = layout.buildDirectory.dir("generated/tachikoma-assets")
val makePayload by tasks.registering(Exec::class) {
    val out = payloadDir.get().file("tachikoma_payload.zip").asFile
    inputs.dir(tachikomaRoot.resolve("tachikoma"))
    inputs.dir(tachikomaRoot.resolve("evolvable"))
    outputs.file(out)
    workingDir = tachikomaRoot
    commandLine(pythonExe, "packaging/build_app.py", "--no-python", "--out", out.absolutePath)
}

android {
    namespace = "com.tachikoma.app"
    compileSdk = 35
    ndkVersion = "27.2.12479018"

    defaultConfig {
        applicationId = "com.tachikoma.app"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DLLAMA_DIR=${projectDir.parentFile.resolve("llama.cpp").absolutePath}",
                    "-DBUILD_SHARED_LIBS=ON",
                    "-DLLAMA_BUILD_COMMON=OFF",
                    "-DLLAMA_BUILD_TESTS=OFF",
                    "-DLLAMA_BUILD_TOOLS=OFF",
                    "-DLLAMA_BUILD_EXAMPLES=OFF",
                    "-DLLAMA_BUILD_SERVER=OFF",
                    "-DLLAMA_BUILD_APP=OFF",
                    "-DLLAMA_OPENSSL=OFF",
                    "-DGGML_NATIVE=OFF",
                    "-DGGML_BACKEND_DL=ON",          // 端末の CPU に合う最適な版の計算部を実行時に選ぶ
                    "-DGGML_CPU_ALL_VARIANTS=ON",
                    "-DGGML_LLAMAFILE=OFF",
                )
            }
        }
    }
    externalNativeBuild {
        cmake {
            path("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }
    sourceSets["main"].assets.srcDir(payloadDir)
    buildTypes {
        release {
            isMinifyEnabled = false
            // 署名鍵は利用者のもの。未設定ならデバッグ鍵で署名して、手元で入れられるようにする
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    packaging {
        jniLibs {
            useLegacyPackaging = true      // 計算部 (libggml-cpu-*.so) をファイルとして展開し、実行時に選べるように
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

tasks.named("preBuild") { dependsOn(makePayload) }

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("certifi")
        }
        pyc {
            src = false
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("androidx.camera:camera-core:1.4.1")
    implementation("androidx.camera:camera-camera2:1.4.1")
    implementation("androidx.camera:camera-lifecycle:1.4.1")
    // 目: 端末の中だけで動く (Google Play 開発者サービス不要の同梱版)
    implementation("com.google.mlkit:face-detection:16.1.7")
    implementation("com.google.mlkit:image-labeling:17.0.9")
    implementation("com.google.mlkit:text-recognition-japanese:16.0.1")
}
