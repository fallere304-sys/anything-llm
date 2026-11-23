package com.z4motioncam;

import android.content.Context;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * Opens the body model if this build has one (PERSON_MODEL=1 build: the TensorFlow Lite classes in
 * the APK and {@link #ASSET} in assets). The runtime class is looked up by name, so the light build
 * compiles and runs without any trace of TensorFlow Lite. Any problem (missing .so for this CPU,
 * damaged model) leaves the finder null and the state "failed": the detector then works on faces and shapes.
 */
final class AndroidBodyModel {
    private static final String TAG = "BodyModel";
    static final String ASSET = "person_ssd.tflite";
    static final String RUNTIME_CLASS = "com.z4motioncam.TfliteRuntime";
    /** Two threads: a quarter of the time of one on a Snapdragon 810 without keeping all big cores hot. */
    static final int THREADS = 2;

    private final BodyFinder finder;
    private final String state;

    private AndroidBodyModel(BodyFinder finder, String state) {
        this.finder = finder;
        this.state = state;
    }

    /** The finder, or null (light build / failed). */
    BodyFinder finder() {
        return finder;
    }

    /** "ok", "none" or "failed". */
    String state() {
        return state;
    }

    static AndroidBodyModel open(Context ctx) {
        Class<?> cls;
        try {
            cls = Class.forName(RUNTIME_CLASS);
        } catch (ClassNotFoundException e) {
            return new AndroidBodyModel(null, "none");
        }
        try {
            ByteBuffer model = readAsset(ctx);
            BodyRuntime rt = (BodyRuntime) cls.getDeclaredConstructor(ByteBuffer.class, int.class).newInstance(model, THREADS);
            return new AndroidBodyModel(new ModelBodyFinder(rt), "ok");
        } catch (FileNotFoundException e) {
            return new AndroidBodyModel(null, "none");
        } catch (Throwable e) {
            Log.w(TAG, "body model not usable", e);
            return new AndroidBodyModel(null, "failed");
        }
    }

    private static ByteBuffer readAsset(Context ctx) throws IOException {
        try (InputStream in = ctx.getAssets().open(ASSET)) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream(5 << 20);
            byte[] b = new byte[64 * 1024];
            for (int n; (n = in.read(b)) > 0; ) bos.write(b, 0, n);
            byte[] all = bos.toByteArray();
            ByteBuffer bb = ByteBuffer.allocateDirect(all.length).order(ByteOrder.nativeOrder());
            bb.put(all);
            bb.rewind();
            return bb;
        }
    }
}
