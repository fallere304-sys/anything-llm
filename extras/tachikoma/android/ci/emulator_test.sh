#!/usr/bin/env bash
# エミュレータでの動作確認: APK を入れ、小型モデルを置き、話しかけて、タチコマが返事をするまで見る。
#   ci/emulator_test.sh <apk> <model.gguf>
set -u
APK="$1"; MODEL="$2"; PKG=com.tachikoma.app
adb install -r "$APK" || exit 1
adb push "$MODEL" /data/local/tmp/model.gguf || exit 1
adb shell "cat /data/local/tmp/model.gguf | run-as $PKG sh -c 'mkdir -p files/models && cat > files/models/model.gguf'" || exit 1
adb shell run-as $PKG ls -la files/models
adb shell pm grant $PKG android.permission.POST_NOTIFICATIONS || true
adb logcat -c
adb shell am start -n $PKG/.MainActivity --es debug_model model.gguf --es debug_say "こんにちは、タチコマ。いま何してるの？"
ok=1
for i in $(seq 1 120); do
  sleep 5
  log=$(adb logcat -d -s tachikoma:V python.stdout:V python.stderr:V tachikoma-llm:V AndroidRuntime:E)
  if echo "$log" | grep -q "FATAL EXCEPTION"; then echo "アプリが落ちた"; break; fi
  if echo "$log" | grep -q "思考ループが止まりました\|モデルを読み込めませんでした"; then echo "思考ループが止まった"; break; fi
  # 起動の挨拶のあとに、もう 1 つ以上タチコマが話していれば (= 話しかけへの返事) 成功
  n=$(echo "$log" | grep -c "TACHIKOMA:")
  if [ "$n" -ge 2 ]; then ok=0; echo "返事を確認 (${i}回目)"; break; fi
done
echo "===== logcat ====="
adb logcat -d -s tachikoma:V python.stdout:V python.stderr:V tachikoma-llm:V AndroidRuntime:E | tail -120
exit $ok
