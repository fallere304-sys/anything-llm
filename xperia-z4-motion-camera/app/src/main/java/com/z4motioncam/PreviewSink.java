package com.z4motioncam;

import android.graphics.SurfaceTexture;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLSurface;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.os.Handler;

/**
 * Off-screen preview target for the camera. Camera1 needs a preview surface even when nothing is
 * shown; this SurfaceTexture is backed by a 1x1 pbuffer and simply latches and discards each frame
 * so the camera's buffer queue never stalls. Nothing is drawn, so GPU load stays negligible.
 *
 * <p>Must be created and released on the thread that owns {@code handler}.
 */
final class PreviewSink implements SurfaceTexture.OnFrameAvailableListener {
    private EGLDisplay display = EGL14.EGL_NO_DISPLAY;
    private EGLContext context = EGL14.EGL_NO_CONTEXT;
    private EGLSurface surface = EGL14.EGL_NO_SURFACE;
    private SurfaceTexture texture;
    private final int[] tex = new int[1];

    PreviewSink(Handler handler) {
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        int[] version = new int[2];
        if (!EGL14.eglInitialize(display, version, 0, version, 1)) {
            throw new IllegalStateException("eglInitialize failed");
        }
        int[] attribs = {
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT,
                EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_NONE
        };
        EGLConfig[] configs = new EGLConfig[1];
        int[] num = new int[1];
        if (!EGL14.eglChooseConfig(display, attribs, 0, configs, 0, 1, num, 0) || num[0] == 0) {
            release();
            throw new IllegalStateException("eglChooseConfig failed");
        }
        context = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT,
                new int[] {EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE}, 0);
        surface = EGL14.eglCreatePbufferSurface(display, configs[0],
                new int[] {EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE}, 0);
        if (context == EGL14.EGL_NO_CONTEXT || surface == EGL14.EGL_NO_SURFACE
                || !EGL14.eglMakeCurrent(display, surface, surface, context)) {
            release();
            throw new IllegalStateException("EGL setup failed");
        }
        GLES20.glGenTextures(1, tex, 0);
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, tex[0]);
        texture = new SurfaceTexture(tex[0]);
        texture.setOnFrameAvailableListener(this, handler);
    }

    SurfaceTexture texture() {
        return texture;
    }

    @Override
    public void onFrameAvailable(SurfaceTexture st) {
        if (texture == null) return;
        try {
            st.updateTexImage();
        } catch (RuntimeException ignored) {
            // released concurrently
        }
    }

    void release() {
        if (texture != null) {
            texture.setOnFrameAvailableListener(null);
            texture.release();
            texture = null;
        }
        if (display != EGL14.EGL_NO_DISPLAY) {
            if (tex[0] != 0) GLES20.glDeleteTextures(1, tex, 0);
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
            if (surface != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(display, surface);
            if (context != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(display, context);
            EGL14.eglTerminate(display);
        }
        display = EGL14.EGL_NO_DISPLAY;
        context = EGL14.EGL_NO_CONTEXT;
        surface = EGL14.EGL_NO_SURFACE;
    }
}
