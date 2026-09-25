package com.z4motioncam;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.nio.ByteBuffer;

/**
 * Encodes NV21 preview frames to an H.264 MP4 with the hardware encoder (MediaCodec + MediaMuxer).
 * Frames come from the preview callback, so recording never has to hand the camera to
 * MediaRecorder and motion detection / live view keep running while recording.
 *
 * <p>Not thread-safe: all calls must come from the camera thread.
 */
@SuppressWarnings("deprecation") // The YUV420 flexible formats are the documented ByteBuffer input on API 21.
final class VideoRecorder {
    private static final String TAG = "VideoRecorder";
    private static final String MIME = "video/avc";

    private final int width;
    private final int height;
    private final int bitrate;
    private final int fps;
    private final int rotation;
    private final String codecName;
    private final int colorFormat;
    private final byte[] converted;

    private MediaCodec codec;
    private MediaMuxer muxer;
    private MediaCodec.BufferInfo info;
    private int track = -1;
    private boolean muxerStarted;
    private int samples;
    private long lastPtsUs = -1;
    private File partFile;
    private long startedAtMs;

    VideoRecorder(int width, int height, int bitrate, int fps, int rotation) throws IOException {
        this.width = width;
        this.height = height;
        this.bitrate = bitrate;
        this.fps = fps;
        this.rotation = rotation;
        int[] chosen = new int[1];
        this.codecName = pickEncoder(chosen);
        this.colorFormat = chosen[0];
        this.converted = new byte[width * height * 3 / 2];
    }

    boolean isRecording() {
        return codec != null;
    }

    long startedAtMs() {
        return startedAtMs;
    }

    File partFile() {
        return partFile;
    }

    void start(File part, long nowMs) throws IOException {
        MediaFormat format = MediaFormat.createVideoFormat(MIME, width, height);
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, colorFormat);
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
        format.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2);
        MediaCodec c = MediaCodec.createByCodecName(codecName);
        try {
            c.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            c.start();
            muxer = new MediaMuxer(part.getAbsolutePath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            muxer.setOrientationHint(rotation);
        } catch (IOException | RuntimeException e) {
            c.release();
            if (muxer != null) muxer.release();
            muxer = null;
            //noinspection ResultOfMethodCallIgnored
            part.delete();
            throw e;
        }
        codec = c;
        info = new MediaCodec.BufferInfo();
        track = -1;
        muxerStarted = false;
        samples = 0;
        lastPtsUs = -1;
        partFile = part;
        startedAtMs = nowMs;
    }

    /** Queues one frame; drops it silently when the encoder has no free input buffer. */
    void encode(byte[] nv21, long ptsUs) {
        if (codec == null) return;
        if (ptsUs <= lastPtsUs) ptsUs = lastPtsUs + 1;
        int idx = codec.dequeueInputBuffer(0);
        if (idx >= 0) {
            ByteBuffer in = codec.getInputBuffer(idx);
            int size = converted.length;
            if (in == null || in.capacity() < size) {
                codec.queueInputBuffer(idx, 0, 0, ptsUs, 0);
            } else {
                convert(nv21, converted, width, height, colorFormat);
                in.clear();
                in.put(converted, 0, size);
                codec.queueInputBuffer(idx, 0, size, ptsUs, 0);
                lastPtsUs = ptsUs;
            }
        }
        drain(false);
    }

    /**
     * Finishes the file. Returns the playable MP4, or null when nothing usable was written.
     */
    File stop() {
        if (codec == null) return null;
        File result = null;
        try {
            int idx = codec.dequeueInputBuffer(10_000);
            if (idx >= 0) {
                codec.queueInputBuffer(idx, 0, 0, Math.max(0, lastPtsUs + 1),
                        MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                drain(true);
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "flush failed", e);
        }
        try {
            codec.stop();
        } catch (RuntimeException e) {
            Log.w(TAG, "codec stop failed", e);
        }
        codec.release();
        codec = null;
        try {
            if (muxerStarted && samples > 0) {
                muxer.stop();
                File dst = RecordingStore.finalFileFor(partFile);
                if (partFile.renameTo(dst)) result = dst;
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "muxer stop failed", e);
        }
        try {
            muxer.release();
        } catch (RuntimeException ignored) {
            // already broken
        }
        muxer = null;
        if (result == null) {
            //noinspection ResultOfMethodCallIgnored
            partFile.delete();
        }
        partFile = null;
        return result;
    }

    private void drain(boolean endOfStream) {
        int idleTries = 0;
        while (true) {
            int idx = codec.dequeueOutputBuffer(info, endOfStream ? 10_000 : 0);
            if (idx == MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (!endOfStream || ++idleTries > 50) return;
            } else if (idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                if (!muxerStarted) {
                    track = muxer.addTrack(codec.getOutputFormat());
                    muxer.start();
                    muxerStarted = true;
                }
            } else if (idx >= 0) {
                ByteBuffer out = codec.getOutputBuffer(idx);
                boolean config = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                if (!config && info.size > 0 && muxerStarted && out != null) {
                    out.position(info.offset);
                    out.limit(info.offset + info.size);
                    muxer.writeSampleData(track, out, info);
                    samples++;
                }
                codec.releaseOutputBuffer(idx, false);
                if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) return;
            }
            // INFO_OUTPUT_BUFFERS_CHANGED needs no handling with getOutputBuffer(int).
        }
    }

    /** NV21 (Y + interleaved VU) to NV12 (Y + UV) or I420 (Y + U + V). */
    static void convert(byte[] nv21, byte[] out, int width, int height, int colorFormat) {
        int ySize = width * height;
        System.arraycopy(nv21, 0, out, 0, ySize);
        int chroma = ySize / 2;
        if (colorFormat == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar) {
            int quarter = ySize / 4;
            for (int i = 0; i < quarter; i++) {
                out[ySize + i] = nv21[ySize + 2 * i + 1];
                out[ySize + quarter + i] = nv21[ySize + 2 * i];
            }
        } else {
            for (int i = 0; i < chroma; i += 2) {
                out[ySize + i] = nv21[ySize + i + 1];
                out[ySize + i + 1] = nv21[ySize + i];
            }
        }
    }

    /** Prefers a hardware AVC encoder that accepts NV12, then I420. */
    private static String pickEncoder(int[] colorFormatOut) throws IOException {
        String fallbackName = null;
        int fallbackFormat = 0;
        for (MediaCodecInfo ci : new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()) {
            if (!ci.isEncoder() || !supports(ci, MIME)) continue;
            int fmt = pickColorFormat(ci.getCapabilitiesForType(MIME).colorFormats);
            if (fmt == 0) continue;
            boolean software = ci.getName().startsWith("OMX.google.") || ci.getName().startsWith("c2.android.");
            if (!software) {
                colorFormatOut[0] = fmt;
                return ci.getName();
            }
            if (fallbackName == null) {
                fallbackName = ci.getName();
                fallbackFormat = fmt;
            }
        }
        if (fallbackName == null) throw new IOException("no usable H.264 encoder");
        colorFormatOut[0] = fallbackFormat;
        return fallbackName;
    }

    private static boolean supports(MediaCodecInfo ci, String mime) {
        for (String t : ci.getSupportedTypes()) {
            if (t.equalsIgnoreCase(mime)) return true;
        }
        return false;
    }

    private static int pickColorFormat(int[] formats) {
        boolean planar = false;
        for (int f : formats) {
            if (f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar) return f;
            if (f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar) planar = true;
        }
        return planar ? MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar : 0;
    }
}
