#!/usr/bin/env bash
# Gradle / Google Maven なしで APK をビルドする。
# 必要: JDK, aapt, dalvik-exchange(dx), zipalign, apksigner, android-sdk-platform-23
#   sudo apt-get install aapt apksigner zipalign android-sdk-platform-23 dalvik-exchange
set -euo pipefail
cd "$(dirname "$0")"

ANDROID_JAR=${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}
BUILD=build
OUT=dist/WifiQuality.apk
KEYSTORE=keystore/release.jks

rm -rf "$BUILD" && mkdir -p "$BUILD"/{gen,classes} dist keystore

echo "[1/6] リソースをコンパイル (aapt)"
aapt package -f -m -J "$BUILD/gen" -M AndroidManifest.xml -S res -I "$ANDROID_JAR" \
  -F "$BUILD/unsigned.apk"

echo "[2/6] Java をコンパイル (Java 8 バイトコード)"
javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 \
  -bootclasspath "$ANDROID_JAR" -d "$BUILD/classes" \
  $(find src "$BUILD/gen" -name '*.java')

echo "[3/6] DEX に変換 (dx)"
dalvik-exchange --dex --min-sdk-version=21 --output="$BUILD/classes.dex" "$BUILD/classes"

echo "[4/6] APK に DEX を追加"
(cd "$BUILD" && zip -q unsigned.apk classes.dex)
zipalign -f 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"

echo "[5/6] 署名鍵を準備"
if [ ! -f "$KEYSTORE" ]; then
  keytool -genkeypair -keystore "$KEYSTORE" -storepass android -keypass android \
    -alias wifiquality -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=WifiQuality, O=Personal, C=JP" >/dev/null 2>&1
fi

echo "[6/6] 署名 (v1 + v2)"
apksigner sign --ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android \
  --ks-key-alias wifiquality --min-sdk-version 21 --out "$OUT" "$BUILD/aligned.apk"
apksigner verify --verbose "$OUT" | head -4
echo "完成: $OUT ($(du -h "$OUT" | cut -f1))"
