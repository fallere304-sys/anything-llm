#!/usr/bin/env bash
# Builds a signed APK without Android Studio / the Android SDK manager, using the Ubuntu packages:
#   sudo apt-get install aapt apksigner zipalign dalvik-exchange android-sdk-platform-23 openjdk-17-jdk
# Usage: KEYSTORE=path/to/key.keystore KS_PASS=... ./build-apk.sh   (a keystore is created if missing)
# Keep the keystore: an APK signed with a different key cannot update an installed one.
set -euo pipefail
cd "$(dirname "$0")"
SRC=app/src/main
OUT=build/manual
ANDROID_JAR=${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}
KEYSTORE=${KEYSTORE:-$HOME/.android/z4motioncam.keystore}
KS_PASS=${KS_PASS:-z4motioncam}
VERSION_CODE=${VERSION_CODE:-1}
VERSION_NAME=${VERSION_NAME:-1.0}

rm -rf "$OUT" && mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex" release

# aapt (v1) needs the package and SDK levels in the manifest; Gradle normally injects them.
sed "s|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"com.z4motioncam\" android:versionCode=\"$VERSION_CODE\" android:versionName=\"$VERSION_NAME\"><uses-sdk android:minSdkVersion=\"21\" android:targetSdkVersion=\"25\" />|" \
    "$SRC/AndroidManifest.xml" > "$OUT/AndroidManifest.xml"

aapt package -f -m -J "$OUT/gen" -M "$OUT/AndroidManifest.xml" -S "$SRC/res" -A "$SRC/assets" \
    -I "$ANDROID_JAR" -F "$OUT/unsigned.apk"
javac --release 8 -Xlint:-options -encoding UTF-8 -cp "$ANDROID_JAR" -d "$OUT/classes" \
    "$OUT/gen/com/z4motioncam/R.java" "$SRC"/java/com/z4motioncam/*.java
dalvik-exchange --dex --min-sdk-version=21 --output="$OUT/dex/classes.dex" "$OUT/classes"
(cd "$OUT/dex" && aapt add ../unsigned.apk classes.dex >/dev/null)
zipalign -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

if [ ! -f "$KEYSTORE" ]; then
    mkdir -p "$(dirname "$KEYSTORE")"
    keytool -genkeypair -keystore "$KEYSTORE" -storepass "$KS_PASS" -keypass "$KS_PASS" \
        -alias z4motioncam -keyalg RSA -keysize 2048 -validity 10950 -dname "CN=Z4 MotionCam"
fi
APK="release/z4motioncam-$VERSION_NAME.apk"
apksigner sign --v4-signing-enabled false --ks "$KEYSTORE" --ks-pass "pass:$KS_PASS" --ks-key-alias z4motioncam --out "$APK" "$OUT/aligned.apk"
apksigner verify --min-sdk-version 21 "$APK"
echo "Built $APK"
