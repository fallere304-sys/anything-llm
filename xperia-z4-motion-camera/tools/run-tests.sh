#!/usr/bin/env bash
# Runs the unit tests without Gradle or the Android SDK: compiles the app and its tests against
# Robolectric's android-all jars (Android 5.0 and 7.1) and runs JUnit on both.
# Needs: openjdk-17-jdk, aapt, android-sdk-platform-23 (sudo apt-get install aapt android-sdk-platform-23 openjdk-17-jdk).
# Usage: tools/run-tests.sh [test class names...]     (default: every *Test class)
# The jars are downloaded once into ~/.cache/z4-test (Maven Central).
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${Z4_TEST_CACHE:-$HOME/.cache/z4-test}"
mkdir -p "$CACHE"
M=https://repo1.maven.org/maven2
fetch() { [ -s "$CACHE/$1" ] || curl -fsSL --retry 4 --retry-delay 5 -o "$CACHE/$1" "$M/$2"; }
fetch android-all-21.jar org/robolectric/android-all/5.0.2_r3-robolectric-r0/android-all-5.0.2_r3-robolectric-r0.jar
fetch android-all-25.jar org/robolectric/android-all/7.1.0_r7-robolectric-r1/android-all-7.1.0_r7-robolectric-r1.jar
fetch junit.jar junit/junit/4.13.2/junit-4.13.2.jar
fetch hamcrest.jar org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar
fetch json.jar org/json/json/20231013/json-20231013.jar

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
mkdir -p "$OUT/gen" "$OUT/classes"
sed 's|<manifest xmlns:android="http://schemas.android.com/apk/res/android">|<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.z4motioncam">|' \
  "$APP/app/src/main/AndroidManifest.xml" > "$OUT/gen/AndroidManifest.xml"
aapt package -f -m -J "$OUT/gen" -M "$OUT/gen/AndroidManifest.xml" -S "$APP/app/src/main/res" \
  -I "${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}"
javac -nowarn -Xlint:-options -encoding UTF-8 \
  -cp "$CACHE/android-all-25.jar:$CACHE/junit.jar:$CACHE/hamcrest.jar:$CACHE/json.jar" -d "$OUT/classes" \
  "$APP"/app/src/main/java/com/z4motioncam/*.java "$OUT/gen/com/z4motioncam/R.java" \
  "$APP"/app/src/test/java/com/z4motioncam/*.java
if [ "$#" -gt 0 ]; then CLASSES="$*"; else
  CLASSES=$(cd "$APP/app/src/test/java" && ls com/z4motioncam/*Test.java | sed 's/\.java//; s#/#.#g' | tr '\n' ' ')
fi
# The tests read files relative to the project folder; the real org.json goes before android-all.
for api in 21 25; do
  echo "== Android API $api"
  (cd "$APP" && java -cp "$OUT/classes:$CACHE/json.jar:$CACHE/android-all-$api.jar:$CACHE/junit.jar:$CACHE/hamcrest.jar" \
    org.junit.runner.JUnitCore $CLASSES 2>&1 | grep -E '^OK|^Tests run|^[0-9]+\)|^\s+at com.z4motioncam')
done
