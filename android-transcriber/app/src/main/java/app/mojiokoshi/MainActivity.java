package app.mojiokoshi;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import java.util.Locale;

/**
 * 画面は 3 状態:
 *   1. 起動画面   … 開始ボタンのみ
 *   2. 文字起こし中 … 真っ黒。タッチすると経過時間・直近の文字・終了ボタンを表示 (一定時間で再び黒)
 *   3. 結果画面   … コピー可能なテキスト
 * 文字起こし自体は {@link TranscriptionService} が行うので、この画面を閉じても止まらない。
 */
public class MainActivity extends Activity implements TranscriptionService.Listener {
    private static final int REQUEST_PERMISSIONS = 1;
    /** 操作パネルを出してから自動で黒画面に戻るまで */
    private static final long CONTROLS_TIMEOUT_MS = 8000;

    private View startScreen;
    private View recordingScreen;
    private View resultScreen;
    private View controls;
    private TextView statusText;
    private TextView elapsedText;
    private TextView previewText;
    private Button stopButton;
    private EditText resultText;
    private TextView savedPath;

    private final Handler handler = new Handler();
    private boolean resultShown;

    private final Runnable hideControls = new Runnable() {
        @Override
        public void run() {
            enterBlackMode();
        }
    };

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            updateElapsed(TranscriptionService.snapshot());
            handler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        startScreen = findViewById(R.id.start_screen);
        recordingScreen = findViewById(R.id.recording_screen);
        resultScreen = findViewById(R.id.result_screen);
        controls = findViewById(R.id.controls);
        statusText = (TextView) findViewById(R.id.status_text);
        elapsedText = (TextView) findViewById(R.id.elapsed_text);
        previewText = (TextView) findViewById(R.id.preview_text);
        stopButton = (Button) findViewById(R.id.stop_button);
        resultText = (EditText) findViewById(R.id.result_text);
        savedPath = (TextView) findViewById(R.id.saved_path);

        findViewById(R.id.start_button).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                onStartClicked();
            }
        });
        recordingScreen.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (controls.getVisibility() == View.VISIBLE) {
                    enterBlackMode();
                } else {
                    showControls();
                }
            }
        });
        stopButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (TranscriptionService.snapshot().phase == TranscriptionService.Phase.IDLE) {
                    // サービスが起動できなかった場合は起動画面へ戻す
                    showScreen(startScreen);
                    return;
                }
                stopButton.setEnabled(false);
                TranscriptionService.stop(MainActivity.this);
            }
        });
        findViewById(R.id.copy_button).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                copyAll();
            }
        });
        findViewById(R.id.share_button).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                share();
            }
        });
        findViewById(R.id.new_button).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                TranscriptionService.acknowledgeResult();
                resultShown = false;
                render(TranscriptionService.snapshot());
            }
        });

        // 音声モデルの展開・読み込みを先に始めておく (開始ボタンを押す頃には準備済みになる)
        ModelManager.prepareAsync(this);
    }

    @Override
    protected void onStart() {
        super.onStart();
        TranscriptionService.setListener(this);
        render(TranscriptionService.snapshot());
        handler.post(tick);
    }

    @Override
    protected void onStop() {
        TranscriptionService.setListener(null);
        handler.removeCallbacks(tick);
        handler.removeCallbacks(hideControls);
        super.onStop();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && recordingScreen.getVisibility() == View.VISIBLE
                && controls.getVisibility() != View.VISIBLE) {
            applyImmersive(true);
        }
    }

    @Override
    public void onBackPressed() {
        if (recordingScreen.getVisibility() == View.VISIBLE) {
            // 文字起こし中に戻るキーで誤って閉じないようにする
            showControls();
        } else if (resultScreen.getVisibility() == View.VISIBLE) {
            TranscriptionService.acknowledgeResult();
            resultShown = false;
            render(TranscriptionService.snapshot());
        } else {
            super.onBackPressed();
        }
    }

    @Override
    public void onTranscriptionChanged() {
        render(TranscriptionService.snapshot());
    }

    // ---- 開始 ----

    private void onStartClicked() {
        if (Build.VERSION.SDK_INT >= 23
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            String[] perms = Build.VERSION.SDK_INT >= 33
                    ? new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS}
                    : new String[]{Manifest.permission.RECORD_AUDIO};
            requestPermissions(perms, REQUEST_PERMISSIONS);
            return;
        }
        startTranscription();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode != REQUEST_PERMISSIONS) {
            return;
        }
        for (int i = 0; i < permissions.length; i++) {
            if (Manifest.permission.RECORD_AUDIO.equals(permissions[i])) {
                if (grantResults[i] == PackageManager.PERMISSION_GRANTED) {
                    startTranscription();
                } else {
                    Toast.makeText(this, R.string.permission_denied, Toast.LENGTH_LONG).show();
                }
                return;
            }
        }
    }

    private void startTranscription() {
        TranscriptionService.acknowledgeResult();
        resultShown = false;
        TranscriptionService.start(this);
        // サービスが状態を RUNNING にした時点で render() が呼ばれるが、押した瞬間に黒くする
        showScreen(recordingScreen);
        enterBlackMode();
    }

    // ---- 描画 ----

    private void render(TranscriptionService.Snapshot s) {
        switch (s.phase) {
            case RUNNING:
            case FINISHING:
                if (recordingScreen.getVisibility() != View.VISIBLE) {
                    showScreen(recordingScreen);
                    enterBlackMode();
                }
                renderRecording(s);
                break;
            case FINISHED:
                showResult(s);
                break;
            case IDLE:
            default:
                // 開始直後 (サービス起動待ち) は黒画面のまま
                if (recordingScreen.getVisibility() != View.VISIBLE) {
                    showScreen(startScreen);
                }
                break;
        }
    }

    private void renderRecording(TranscriptionService.Snapshot s) {
        if (controls.getVisibility() != View.VISIBLE) {
            // 黒画面中は描画しない (タッチで操作パネルを出したときに最新状態を描く)
            return;
        }
        if (s.phase == TranscriptionService.Phase.FINISHING) {
            statusText.setText(R.string.finishing);
            stopButton.setEnabled(false);
        } else {
            if (!s.modelReady) {
                statusText.setText(R.string.loading_model);
            } else if (s.backlogMs >= 2000) {
                // 端末の処理が録音に追いついていない量を出す (性能評価の目安)
                statusText.setText(getString(R.string.listening_backlog, s.backlogMs / 1000));
            } else {
                statusText.setText(R.string.listening);
            }
            stopButton.setEnabled(true);
        }
        previewText.setText(s.recent + s.partial);
        updateElapsed(s);
    }

    private void updateElapsed(TranscriptionService.Snapshot s) {
        if (s.phase != TranscriptionService.Phase.RUNNING && s.phase != TranscriptionService.Phase.FINISHING) {
            return;
        }
        long end = s.endElapsedMs != 0 ? s.endElapsedMs : SystemClock.elapsedRealtime();
        long sec = Math.max(0, (end - s.startElapsedMs) / 1000);
        elapsedText.setText(String.format(Locale.US, "%02d:%02d:%02d", sec / 3600, (sec / 60) % 60, sec % 60));
    }

    private void showResult(TranscriptionService.Snapshot s) {
        if (resultScreen.getVisibility() != View.VISIBLE) {
            showScreen(resultScreen);
        }
        if (resultShown) {
            // ユーザーが編集中かもしれないので上書きしない
            return;
        }
        resultShown = true;
        String body = s.text.trim();
        resultText.setText(body.isEmpty() ? "" : body + "\n");
        resultText.setHint(R.string.empty_result);
        StringBuilder info = new StringBuilder();
        if (s.error != null) {
            info.append(getString(R.string.error_prefix, s.error)).append('\n');
            Toast.makeText(this, s.error, Toast.LENGTH_LONG).show();
        }
        if (s.warning != null) {
            info.append(s.warning).append('\n');
        }
        if (s.file != null && s.file.exists()) {
            info.append(getString(R.string.saved_to, s.file.getAbsolutePath()));
        }
        savedPath.setText(info.toString().trim());
    }

    private void showScreen(View screen) {
        boolean recording = screen == recordingScreen;
        startScreen.setVisibility(screen == startScreen ? View.VISIBLE : View.GONE);
        recordingScreen.setVisibility(recording ? View.VISIBLE : View.GONE);
        resultScreen.setVisibility(screen == resultScreen ? View.VISIBLE : View.GONE);
        if (!recording) {
            handler.removeCallbacks(hideControls);
            getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            setBrightness(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE);
            applyImmersive(false);
        }
    }

    // ---- 黒画面 / 操作パネル ----

    private void enterBlackMode() {
        handler.removeCallbacks(hideControls);
        controls.setVisibility(View.GONE);
        // タッチで復帰できるよう画面自体は点けたまま、バックライトを最低にする
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setBrightness(0f);
        applyImmersive(true);
    }

    private void showControls() {
        controls.setVisibility(View.VISIBLE);
        setBrightness(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE);
        render(TranscriptionService.snapshot());
        handler.removeCallbacks(hideControls);
        handler.postDelayed(hideControls, CONTROLS_TIMEOUT_MS);
    }

    private void setBrightness(float value) {
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = value;
        getWindow().setAttributes(lp);
    }

    private void applyImmersive(boolean on) {
        View decor = getWindow().getDecorView();
        if (on) {
            decor.setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        } else {
            decor.setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
        }
    }

    // ---- 結果の出力 ----

    private void copyAll() {
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText(getString(R.string.app_name), resultText.getText().toString()));
        Toast.makeText(this, R.string.copied, Toast.LENGTH_SHORT).show();
    }

    private void share() {
        Intent send = new Intent(Intent.ACTION_SEND)
                .setType("text/plain")
                .putExtra(Intent.EXTRA_TEXT, resultText.getText().toString());
        startActivity(Intent.createChooser(send, getString(R.string.share)));
    }
}
