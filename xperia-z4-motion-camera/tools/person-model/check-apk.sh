#!/usr/bin/env bash
# Structure check of a built APK. Usage: check-apk.sh <apk> light|model
# light: no TensorFlow Lite, no model.  model: model in assets, .so for arm64-v8a and armeabi-v7a only,
# TfliteRuntime + TensorFlow Lite classes in the dex, 4-byte aligned, signature valid for API 21.
set -euo pipefail
apk=$1; kind=$2
list=$(aapt list "$apk")
has() { grep -qx "$1" <<<"$list"; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
unzip -oq "$apk" classes.dex -d "$tmp"
zipalign -c 4 "$apk"
apksigner verify --min-sdk-version 21 "$apk" >/dev/null 2>&1
has classes.dex
case $kind in
light)
    ! grep -q '^lib/' <<<"$list" && ! has assets/person_ssd.tflite
    ! grep -q 'org/tensorflow' "$tmp/classes.dex"
    ! grep -q 'TfliteRuntime' "$tmp/classes.dex" ;;
model)
    has assets/person_ssd.tflite && has assets/LICENSE-tensorflow-lite.txt && has assets/NOTICE-person-model.txt
    has lib/arm64-v8a/libtensorflowlite_jni.so && has lib/armeabi-v7a/libtensorflowlite_jni.so
    ! grep -q '^lib/x86' <<<"$list"
    grep -q 'Lorg/tensorflow/lite/Interpreter;' "$tmp/classes.dex"
    grep -q 'Lorg/tensorflow/lite/Tensor;' "$tmp/classes.dex"
    grep -q 'Lcom/z4motioncam/TfliteRuntime;' "$tmp/classes.dex" ;;
*) echo "light|model" >&2; exit 2 ;;
esac
echo "OK: $apk ($kind)"
