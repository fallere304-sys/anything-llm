package app.btfiletransfer.storage;

import android.Manifest;
import android.annotation.TargetApi;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.MimeTypeMap;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/**
 * 受信ファイルを「ダウンロード/BtFileTransfer」に保存する。
 * Android 10 以降は MediaStore、9 以前（Xperia Z4 は 5.0〜7.0）は直接ファイルに書く。
 */
public final class ReceivedFileStore {
    public static final String FOLDER = "BtFileTransfer";

    /** 書き込み中のファイル。commit() で確定、abort() で削除。 */
    public interface Pending {
        OutputStream stream();

        /** 表示用の保存場所。 */
        String location();

        /** 確定後に他アプリで開くための Uri（取得できなければ null）。 */
        Uri commit() throws IOException;

        void abort();
    }

    private final Context context;

    public ReceivedFileStore(Context context) {
        this.context = context.getApplicationContext();
    }

    public Pending create(String safeName, String mimeType) throws IOException {
        if (mimeType == null || mimeType.isEmpty()) mimeType = guessMime(safeName);
        if (Build.VERSION.SDK_INT >= 29) return createMediaStore(safeName, mimeType);
        return createFile(safeName, mimeType);
    }

    /** 保存先フォルダ（表示用）。 */
    public String folderDescription() {
        if (Build.VERSION.SDK_INT >= 29 || canWritePublic()) {
            return Environment.DIRECTORY_DOWNLOADS + "/" + FOLDER;
        }
        File dir = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        return dir == null ? "(外部ストレージなし)" : dir.getAbsolutePath();
    }

    // ---- Android 10+ ----

    @TargetApi(29) // 呼び出し元 create() で SDK_INT >= 29 を確認済み
    private Pending createMediaStore(String name, String mimeType) throws IOException {
        final ContentResolver cr = context.getContentResolver();
        ContentValues v = new ContentValues();
        v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        v.put(MediaStore.MediaColumns.MIME_TYPE, mimeType);
        v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + FOLDER);
        v.put(MediaStore.MediaColumns.IS_PENDING, 1);
        final Uri uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
        if (uri == null) throw new IOException("MediaStore に保存できません");
        final OutputStream os = cr.openOutputStream(uri);
        if (os == null) {
            cr.delete(uri, null, null);
            throw new IOException("保存先を開けません");
        }
        final String location = Environment.DIRECTORY_DOWNLOADS + "/" + FOLDER + "/" + name;
        return new Pending() {
            @Override
            public OutputStream stream() {
                return os;
            }

            @Override
            public String location() {
                return location;
            }

            @Override
            public Uri commit() throws IOException {
                os.close();
                ContentValues done = new ContentValues();
                done.put(MediaStore.MediaColumns.IS_PENDING, 0);
                cr.update(uri, done, null, null);
                return uri;
            }

            @Override
            public void abort() {
                closeQuietly(os);
                cr.delete(uri, null, null);
            }
        };
    }

    // ---- Android 9 以前 ----

    private Pending createFile(String name, final String mimeType) throws IOException {
        File dir;
        if (canWritePublic()) {
            dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), FOLDER);
        } else {
            // ストレージ権限が拒否された場合はアプリ専用領域（権限不要）に保存
            dir = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
            if (dir == null) dir = new File(context.getFilesDir(), "received");
        }
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("フォルダを作成できません: " + dir);
        File f = new File(dir, name);
        for (int n = 2; f.exists(); n++) f = new File(dir, FileNames.numbered(name, n));
        final File file = f;
        final OutputStream os = new FileOutputStream(file);
        return new Pending() {
            @Override
            public OutputStream stream() {
                return os;
            }

            @Override
            public String location() {
                return file.getAbsolutePath();
            }

            @Override
            public Uri commit() throws IOException {
                os.close();
                // ギャラリー等に反映。Android 7 以降は file:// を他アプリへ渡せないので
                // 開く用途には MediaStore の content:// を使う
                final Uri[] result = new Uri[1];
                final Object lock = new Object();
                MediaScannerConnection.scanFile(context, new String[] {file.getAbsolutePath()},
                        new String[] {mimeType}, (path, uri) -> {
                            synchronized (lock) {
                                result[0] = uri;
                                lock.notifyAll();
                            }
                        });
                synchronized (lock) {
                    long deadline = System.currentTimeMillis() + 3000;
                    while (result[0] == null && System.currentTimeMillis() < deadline) {
                        try {
                            lock.wait(Math.max(1, deadline - System.currentTimeMillis()));
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                    }
                }
                if (result[0] != null) return result[0];
                return Build.VERSION.SDK_INT < 24 ? Uri.fromFile(file) : null;
            }

            @Override
            public void abort() {
                closeQuietly(os);
                //noinspection ResultOfMethodCallIgnored
                file.delete();
            }
        };
    }

    private boolean canWritePublic() {
        if (Build.VERSION.SDK_INT >= 29) return false;
        if (!Environment.MEDIA_MOUNTED.equals(Environment.getExternalStorageState())) return false;
        return Build.VERSION.SDK_INT < 23
                || context.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED;
    }

    public static String guessMime(String name) {
        int dot = name.lastIndexOf('.');
        if (dot >= 0) {
            String m = MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substring(dot + 1).toLowerCase());
            if (m != null) return m;
        }
        return "application/octet-stream";
    }

    private static void closeQuietly(OutputStream os) {
        try {
            os.close();
        } catch (IOException ignored) {
            // 破棄するファイルなので無視
        }
    }
}
