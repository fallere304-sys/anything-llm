#!/usr/bin/env bash
# Downloads the three third-party pieces of the PERSON_MODEL=1 build and checks their SHA-256:
#   * COCO SSD MobileNet v1 (quantized, 300x300), Apache-2.0, 4.2 MB   (download.tensorflow.org, TF Lite model zoo)
#   * org.tensorflow:tensorflow-lite 2.8.0 .aar (Java classes + libtensorflowlite_jni.so per ABI, Java 7 bytecode, minSdk 19) and
#     org.tensorflow:tensorflow-lite-api 2.8.0 .aar (the public Interpreter/Tensor classes it builds on), Apache-2.0 (Maven Central)
# and unpacks them into $1 (default build/model-cache): detect.tflite, labelmap.txt, tflite/classes.jar,
# tflite/api-classes.jar, tflite/jni/<abi>/libtensorflowlite_jni.so, tflite/LICENSE. Nothing is committed to git; skipped when already there.
set -euo pipefail
DEST=${1:-build/model-cache}
MODEL_URL=https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
MODEL_SHA=a809cd290b4d6a2e8a9d5dad076e0bd695b8091974e0eed1052b480b2f21b6dc
AAR_URL=https://repo1.maven.org/maven2/org/tensorflow/tensorflow-lite/2.8.0/tensorflow-lite-2.8.0.aar
AAR_SHA=cafbaa8329c073a2a4ce0ca74529d8c356638753abbc4401b7739b18fa4ae8bc
API_URL=https://repo1.maven.org/maven2/org/tensorflow/tensorflow-lite-api/2.8.0/tensorflow-lite-api-2.8.0.aar
API_SHA=3641fdc26019e2dc250a94dd7b26831b3ced361caa21cefd1f50e98aa64c93a3
if [ -f "$DEST/detect.tflite" ] && [ -f "$DEST/tflite/classes.jar" ] && [ -f "$DEST/tflite/api-classes.jar" ] && [ -d "$DEST/tflite/jni" ]; then exit 0; fi
mkdir -p "$DEST/tflite"
fetch() { # url sha out
    curl -fsSL --retry 2 -o "$3" "$1"
    echo "$2  $3" | sha256sum -c - >/dev/null || { echo "checksum mismatch: $1" >&2; rm -f "$3"; exit 1; }
}
fetch "$MODEL_URL" "$MODEL_SHA" "$DEST/ssd.zip"
fetch "$AAR_URL" "$AAR_SHA" "$DEST/tflite.aar"
fetch "$API_URL" "$API_SHA" "$DEST/api.aar"
unzip -oq "$DEST/ssd.zip" detect.tflite labelmap.txt -d "$DEST"
unzip -oq "$DEST/tflite.aar" classes.jar 'jni/*' LICENSE -d "$DEST/tflite"
unzip -p "$DEST/api.aar" classes.jar > "$DEST/tflite/api-classes.jar"
rm -f "$DEST/ssd.zip" "$DEST/tflite.aar" "$DEST/api.aar"
