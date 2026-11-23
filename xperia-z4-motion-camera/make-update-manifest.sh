#!/usr/bin/env bash
# Writes release/update.json for an APK that was already built and signed (this script builds and
# signs nothing). The app reads this file, compares versionCode, downloads the APK next to it and
# checks its SHA-256 and signature before offering the install.
# Usage: ./make-update-manifest.sh release/z4motioncam-1.12.apk 12 1.12 ["release notes"]
#   (the versionCode / versionName must be the ones the APK was built with: VERSION_CODE / VERSION_NAME)
# Publish update.json and the APK in the same folder over https. For a second channel, send the printed
# SHA-256 to the phone's owner separately; they can paste it into the app's "期待する APK の SHA-256".
set -euo pipefail
cd "$(dirname "$0")"
APK=${1:?usage: $0 <apk> <versionCode> <versionName> [notes]}
CODE=${2:?versionCode}
NAME=${3:?versionName}
NOTES=${4:-}
[[ "$CODE" =~ ^[0-9]+$ ]] || { echo "versionCode must be a number" >&2; exit 1; }
[[ "$NAME" =~ ^[A-Za-z0-9._-]{1,32}$ ]] || { echo "versionName: letters, digits . _ - only" >&2; exit 1; }
[ -f "$APK" ] || { echo "no such file: $APK" >&2; exit 1; }
SHA=$(sha256sum "$APK" | cut -d' ' -f1)
SIZE=$(stat -c %s "$APK")
OUT="$(dirname "$APK")/update.json"
# notes: JSON-escape backslash and double quote, flatten line breaks
ESC=${NOTES//\\/\\\\}
ESC=${ESC//\"/\\\"}
ESC=${ESC//$'\n'/ }
printf '{\n  "package": "com.z4motioncam",\n  "versionCode": %s,\n  "versionName": "%s",\n  "apk": "%s",\n  "sha256": "%s",\n  "size": %s,\n  "notes": "%s"\n}\n' \
    "$CODE" "$NAME" "$(basename "$APK")" "$SHA" "$SIZE" "$ESC" > "$OUT"
echo "Wrote $OUT"
echo "SHA-256 $SHA"
