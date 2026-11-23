#!/usr/bin/env bash
# Builds a signed APK without Android Studio / the Android SDK manager, using the Ubuntu packages:
#   sudo apt-get install aapt apksigner zipalign dalvik-exchange android-sdk-platform-23 openjdk-17-jdk
# Usage: KEYSTORE=path/to/key.keystore KS_PASS=... ./build-apk.sh   (a keystore is created if missing)
# Keep the keystore: an APK signed with a different key cannot update an installed one.
# EXTERNAL_HOST=203.0.113.5 presets the "external address" setting for a personal build. Such an APK
# contains your home IP: keep it private (APK_NAME lets you write it outside release/).
# UPDATE_URL=https://.../update.json presets the update source (see make-update-manifest.sh).
# PERSON_MODEL=1 builds the heavier edition with the body model (COCO SSD MobileNet, Apache-2.0) and
# TensorFlow Lite (arm64-v8a + armeabi-v7a); both are fetched once into build/model-cache by
# tools/person-model/fetch.sh (MODEL_CACHE=dir to use another folder; SHA-256 is checked). Without it
# the APK is the light one (about 100 KB) and the person modes use faces and shapes only.
set -euo pipefail
cd "$(dirname "$0")"
SRC=app/src/main
OUT=build/manual
ANDROID_JAR=${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}
KEYSTORE=${KEYSTORE:-$HOME/.android/z4motioncam.keystore}
KS_PASS=${KS_PASS:-z4motioncam}
VERSION_CODE=${VERSION_CODE:-1}
VERSION_NAME=${VERSION_NAME:-1.0}
EXTERNAL_HOST=${EXTERNAL_HOST:-}
PERSON_MODEL=${PERSON_MODEL:-0}
MODEL_CACHE=${MODEL_CACHE:-build/model-cache}
if [ "$PERSON_MODEL" = 1 ]; then SUFFIX=-model; else SUFFIX=; fi
APK=${APK_NAME:-release/z4motioncam-$VERSION_NAME$SUFFIX.apk}

if [ "$PERSON_MODEL" = 1 ]; then tools/person-model/fetch.sh "$MODEL_CACHE"; fi
rm -rf "$OUT" && mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex" "$(dirname "$APK")"

# Body-model edition: the model and the licence texts go to assets, TensorFlow Lite's classes are
# compiled against and dexed with the app, its .so files go to lib/<abi>/.
ASSETS=(-A "$SRC/assets")
EXTRA_SRC=()
CP="$ANDROID_JAR"
DEXIN=("$OUT/classes")
NOCOMPRESS=()
if [ "$PERSON_MODEL" = 1 ]; then
    mkdir -p "$OUT/model-assets"
    cp "$MODEL_CACHE/detect.tflite" "$OUT/model-assets/person_ssd.tflite"
    cp "$MODEL_CACHE/tflite/LICENSE" "$OUT/model-assets/LICENSE-tensorflow-lite.txt"
    cp tools/person-model/NOTICE-person-model.txt "$OUT/model-assets/NOTICE-person-model.txt"
    ASSETS+=(-A "$OUT/model-assets")
    NOCOMPRESS=(-0 tflite)
    EXTRA_SRC=(app/src/model/java/com/z4motioncam/*.java)
    CP="$ANDROID_JAR:$MODEL_CACHE/tflite/classes.jar:$MODEL_CACHE/tflite/api-classes.jar"
    DEXIN+=("$MODEL_CACHE/tflite/classes.jar" "$MODEL_CACHE/tflite/api-classes.jar")
fi

# Resource overlay for a personal build: listed first, so it wins over app/src/main/res.
# EXTERNAL_HOST presets the external address; UPDATE_URL presets where the app looks for updates
# (https only; the default points to this repository's release folder).
OVERLAY=()
if [ -n "$EXTERNAL_HOST" ] || [ -n "${UPDATE_URL:-}" ]; then
    mkdir -p "$OUT/preset/values"
    {
        printf '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        if [ -n "$EXTERNAL_HOST" ]; then
            [[ "$EXTERNAL_HOST" =~ ^[A-Za-z0-9.:-]+$ ]] || { echo "invalid EXTERNAL_HOST" >&2; exit 1; }
            printf '    <string name="default_external_host" translatable="false">%s</string>\n' "$EXTERNAL_HOST"
        fi
        if [ -n "${UPDATE_URL:-}" ]; then
            [[ "$UPDATE_URL" =~ ^https://[A-Za-z0-9._~:/?#@!+,=%-]+$ ]] || { echo "UPDATE_URL must be a plain https:// URL" >&2; exit 1; }
            printf '    <string name="default_update_url" translatable="false">%s</string>\n' "$UPDATE_URL"
        fi
        printf '</resources>\n'
    } > "$OUT/preset/values/preset.xml"
    OVERLAY=(-S "$OUT/preset")
fi

# aapt (v1) needs the package and SDK levels in the manifest; Gradle normally injects them.
sed "s|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"com.z4motioncam\" android:versionCode=\"$VERSION_CODE\" android:versionName=\"$VERSION_NAME\"><uses-sdk android:minSdkVersion=\"21\" android:targetSdkVersion=\"25\" />|" \
    "$SRC/AndroidManifest.xml" > "$OUT/AndroidManifest.xml"

aapt package -f -m -J "$OUT/gen" -M "$OUT/AndroidManifest.xml" "${OVERLAY[@]}" -S "$SRC/res" "${ASSETS[@]}" "${NOCOMPRESS[@]}" \
    -I "$ANDROID_JAR" -F "$OUT/unsigned.apk"
javac --release 8 -Xlint:-options -encoding UTF-8 -cp "$CP" -d "$OUT/classes" \
    "$OUT/gen/com/z4motioncam/R.java" "$SRC"/java/com/z4motioncam/*.java "${EXTRA_SRC[@]}"
dalvik-exchange --dex --min-sdk-version=21 --output="$OUT/dex/classes.dex" "${DEXIN[@]}"
(cd "$OUT/dex" && aapt add ../unsigned.apk classes.dex >/dev/null)
if [ "$PERSON_MODEL" = 1 ]; then
    for abi in arm64-v8a armeabi-v7a; do
        mkdir -p "$OUT/libs/lib/$abi"
        cp "$MODEL_CACHE/tflite/jni/$abi/libtensorflowlite_jni.so" "$OUT/libs/lib/$abi/"
    done
    (cd "$OUT/libs" && aapt add ../unsigned.apk lib/arm64-v8a/libtensorflowlite_jni.so lib/armeabi-v7a/libtensorflowlite_jni.so >/dev/null)
fi
zipalign -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

if [ ! -f "$KEYSTORE" ]; then
    mkdir -p "$(dirname "$KEYSTORE")"
    keytool -genkeypair -keystore "$KEYSTORE" -storepass "$KS_PASS" -keypass "$KS_PASS" \
        -alias z4motioncam -keyalg RSA -keysize 2048 -validity 10950 -dname "CN=Z4 MotionCam"
fi
apksigner sign --v4-signing-enabled false --ks "$KEYSTORE" --ks-pass "pass:$KS_PASS" --ks-key-alias z4motioncam --out "$APK" "$OUT/aligned.apk"
apksigner verify --min-sdk-version 21 "$APK"
echo "Built $APK"
sha256sum "$APK"
