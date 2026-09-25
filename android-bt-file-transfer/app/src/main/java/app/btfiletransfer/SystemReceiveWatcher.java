package app.btfiletransfer;

import android.os.Environment;
import android.os.FileObserver;
import android.os.Handler;
import android.os.Looper;
import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * Android 標準の Bluetooth 受信機能（システムの OPP サーバ）が保存したファイルを検知する。
 *
 * <p>Android では OS 自身も OBEX Object Push サーバを持っており、Windows がどちらの SDP レコードに
 * 接続するかは端末次第。システム側で受信された場合でも、このアプリの履歴に表示できるようにする。
 * スコープドストレージ以前（Android 9 以下。Xperia Z4 は該当）のみ動作する。
 */
final class SystemReceiveWatcher {
    interface Listener {
        void onSystemReceived(File file);
    }

    private final List<FileObserver> observers = new ArrayList<>();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Listener listener;

    SystemReceiveWatcher(Listener listener) {
        this.listener = listener;
    }

    static List<File> candidateDirs() {
        // AOSP の既定は /sdcard/bluetooth。機種・バージョンにより Download 配下のこともある。
        // 内部ストレージは大文字小文字を区別しないため、実在するディレクトリ名を列挙して重複監視を避ける
        File[] parents = {
                Environment.getExternalStorageDirectory(),
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
        };
        List<File> dirs = new ArrayList<>();
        for (File parent : parents) {
            File[] children = parent.listFiles();
            if (children == null) continue;
            for (File c : children) {
                if (c.isDirectory() && c.getName().equalsIgnoreCase("bluetooth")) dirs.add(c);
            }
        }
        return dirs;
    }

    @SuppressWarnings("deprecation") // FileObserver(String, int) は API 29 で非推奨だが対象端末では唯一の手段
    void start() {
        stop();
        for (final File dir : candidateDirs()) {
            if (!dir.isDirectory()) continue;
            FileObserver o = new FileObserver(dir.getAbsolutePath(), FileObserver.CLOSE_WRITE | FileObserver.MOVED_TO) {
                @Override
                public void onEvent(int event, final String path) {
                    if (path == null) return;
                    final File f = new File(dir, path);
                    // 受信失敗時はシステムが直後に削除するので、少し待って残っているものだけ通知
                    main.postDelayed(() -> {
                        if (f.isFile() && f.length() > 0) listener.onSystemReceived(f);
                    }, 1500);
                }
            };
            o.startWatching();
            observers.add(o);
        }
    }

    void stop() {
        for (FileObserver o : observers) o.stopWatching();
        observers.clear();
    }
}
