#!/bin/sh
# Rebuilds the signed fake APKs used by the push-update tests. Needs python3, keytool and jarsigner.
# The keys are throw-away test keys created here and deleted afterwards: nothing secret is kept.
# signed-a / signed-a2 share key A (same signer, different content); signed-b uses key B.
set -e
cd "$(dirname "$0")"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
mk() { # name seed withDex
python3 - "$1" "$2" "$3" "$W" <<'PY'
import sys, zipfile, random
name, seed, dex, w = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1", sys.argv[4]
r = random.Random(seed)
z = zipfile.ZipFile(w + "/" + name, "w", zipfile.ZIP_STORED)
z.writestr(zipfile.ZipInfo("AndroidManifest.xml", (2020, 1, 1, 0, 0, 0)), bytes([3, 0, 8, 0]))
if dex:
    z.writestr(zipfile.ZipInfo("classes.dex", (2020, 1, 1, 0, 0, 0)), bytes(r.getrandbits(8) for _ in range(30000)))
if not dex:
    z.writestr(zipfile.ZipInfo("assets/pad.bin", (2020, 1, 1, 0, 0, 0)), bytes(r.getrandbits(8) for _ in range(30000)))
z.writestr(zipfile.ZipInfo("assets/marker.txt", (2020, 1, 1, 0, 0, 0)), ("build %d\n" % seed).encode())
z.close()
PY
}
for k in a b; do
  keytool -genkeypair -keystore "$W/$k.jks" -storepass testtest -keypass testtest -alias $k \
    -keyalg RSA -keysize 2048 -validity 9000 -dname "CN=test-$k" >/dev/null 2>&1
done
mk signed-a.apk 1 1; mk signed-a2.apk 2 1; mk signed-b.apk 3 1; mk signed-nodex.apk 4 0; mk unsigned.apk 5 1
jarsigner -keystore "$W/a.jks" -storepass testtest -keypass testtest -digestalg SHA-256 -sigalg SHA256withRSA "$W/signed-a.apk" a >/dev/null
jarsigner -keystore "$W/a.jks" -storepass testtest -keypass testtest -digestalg SHA-256 -sigalg SHA256withRSA "$W/signed-a2.apk" a >/dev/null
jarsigner -keystore "$W/b.jks" -storepass testtest -keypass testtest -digestalg SHA-256 -sigalg SHA256withRSA "$W/signed-b.apk" b >/dev/null
jarsigner -keystore "$W/a.jks" -storepass testtest -keypass testtest -digestalg SHA-256 -sigalg SHA256withRSA "$W/signed-nodex.apk" a >/dev/null
cp "$W"/*.apk .
ls -l *.apk
