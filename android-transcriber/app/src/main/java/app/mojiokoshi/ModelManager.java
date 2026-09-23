package app.mojiokoshi;

import android.content.Context;
import android.content.res.AssetManager;
import android.util.Log;

import org.vosk.LibVosk;
import org.vosk.LogLevel;
import org.vosk.Model;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;

/**
 * APK に同梱した Vosk 日本語モデルを内部ストレージへ展開し、読み込んで保持する。
 * 展開 (初回のみ、数十秒) と読み込み (数秒) はアプリ起動直後にバックグラウンドで始め、
 * 開始ボタンが押された時点で待たずに済むようにする。
 */
final class ModelManager {
    private static final String TAG = "ModelManager";
    private static final String ASSET_DIR = "model-ja";
    private static final String VERSION_FILE = "version.txt";

    private static final Object LOCK = new Object();
    private static Thread loader;
    private static Model model;
    private static IOException error;

    private ModelManager() {
    }

    /** バックグラウンドでモデルの準備を開始する (多重呼び出し可)。 */
    static void prepareAsync(Context context) {
        final Context app = context.getApplicationContext();
        synchronized (LOCK) {
            if (model != null || loader != null) {
                return;
            }
            error = null;
            loader = new Thread(new Runnable() {
                @Override
                public void run() {
                    Model loaded = null;
                    IOException failure = null;
                    try {
                        LibVosk.setLogLevel(LogLevel.WARNINGS);
                        File dir = syncAssets(app);
                        loaded = new Model(dir.getAbsolutePath());
                    } catch (IOException e) {
                        failure = e;
                    } catch (RuntimeException | UnsatisfiedLinkError e) {
                        failure = new IOException(e.toString(), e);
                    }
                    synchronized (LOCK) {
                        model = loaded;
                        error = failure;
                        loader = null;
                        LOCK.notifyAll();
                    }
                    if (failure != null) {
                        Log.e(TAG, "model load failed", failure);
                    }
                }
            }, "vosk-model-loader");
            loader.start();
        }
    }

    static boolean isReady() {
        synchronized (LOCK) {
            return model != null;
        }
    }

    /** モデルの準備完了を待って返す。失敗していれば IOException。 */
    static Model await(Context context) throws IOException, InterruptedException {
        prepareAsync(context);
        synchronized (LOCK) {
            while (model == null && error == null) {
                LOCK.wait();
            }
            if (model != null) {
                return model;
            }
            IOException e = error;
            // 次回呼び出し時に再試行できるようにしておく
            error = null;
            throw e;
        }
    }

    private static File syncAssets(Context context) throws IOException {
        AssetManager assets = context.getAssets();
        String bundled = readAll(assets.open(ASSET_DIR + "/" + VERSION_FILE));
        File target = new File(context.getFilesDir(), ASSET_DIR);
        File versionFile = new File(target, VERSION_FILE);
        if (versionFile.exists() && bundled.equals(readAll(new FileInputStream(versionFile)))) {
            return target;
        }

        Log.i(TAG, "extracting model " + bundled);
        File tmp = new File(context.getFilesDir(), ASSET_DIR + ".tmp");
        deleteRecursive(tmp);
        copyAssetDir(assets, ASSET_DIR, tmp);
        deleteRecursive(target);
        if (!tmp.renameTo(target)) {
            throw new IOException("cannot rename " + tmp + " to " + target);
        }
        return target;
    }

    private static void copyAssetDir(AssetManager assets, String path, File dest) throws IOException {
        String[] children = assets.list(path);
        if (children == null || children.length == 0) {
            // ファイル
            File parent = dest.getParentFile();
            if (parent != null && !parent.exists() && !parent.mkdirs()) {
                throw new IOException("cannot create " + parent);
            }
            InputStream in = assets.open(path);
            try {
                OutputStream out = new FileOutputStream(dest);
                try {
                    byte[] buf = new byte[64 * 1024];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        out.write(buf, 0, n);
                    }
                } finally {
                    out.close();
                }
            } finally {
                in.close();
            }
            return;
        }
        if (!dest.exists() && !dest.mkdirs()) {
            throw new IOException("cannot create " + dest);
        }
        for (String child : children) {
            copyAssetDir(assets, path + "/" + child, new File(dest, child));
        }
    }

    private static void deleteRecursive(File f) {
        File[] children = f.listFiles();
        if (children != null) {
            for (File c : children) {
                deleteRecursive(c);
            }
        }
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    private static String readAll(InputStream in) throws IOException {
        try {
            BufferedReader r = new BufferedReader(new InputStreamReader(in, "UTF-8"));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line);
            }
            return sb.toString().trim();
        } finally {
            in.close();
        }
    }
}
