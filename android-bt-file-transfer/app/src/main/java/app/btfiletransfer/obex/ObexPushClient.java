package app.btfiletransfer.obex;

import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * OBEX Object Push クライアント。Windows の「ファイルを受信する」(fsquirt) へ送信する側。
 * 1 セッション: connect() → put() を任意回 → disconnect()。
 */
public final class ObexPushClient {
    public interface ProgressListener {
        void onProgress(long sent, long total);
    }

    private final InputStream in;
    private final OutputStream out;
    private final int localMaxPacket;
    private int maxPacket = ObexConstants.MIN_PACKET_SIZE;
    private long connectionId = -1;
    private volatile boolean cancelled;

    public ObexPushClient(InputStream in, OutputStream out) {
        this(in, out, ObexConstants.MAX_PACKET_SIZE);
    }

    public ObexPushClient(InputStream in, OutputStream out, int localMaxPacket) {
        this.in = in;
        this.out = out;
        this.localMaxPacket = localMaxPacket;
    }

    public void connect() throws IOException {
        ObexPacket.write(out, ObexConstants.OP_CONNECT, ObexPacket.connectFields(localMaxPacket));
        ObexPacket rsp = readResponse();
        if (rsp.code != ObexConstants.RSP_OK) {
            throw new ObexException(rsp.code, "相手が接続を拒否しました");
        }
        int peerMax = rsp.connectMaxPacket();
        maxPacket = Math.max(ObexConstants.MIN_PACKET_SIZE, Math.min(localMaxPacket, peerMax));
        connectionId = rsp.headers(4).connectionId;
    }

    public int negotiatedMaxPacket() {
        return maxPacket;
    }

    /** 別スレッドから呼ぶと、進行中の put() を ABORT して例外で終了させる。 */
    public void cancel() {
        cancelled = true;
    }

    /**
     * 1 ファイルを送信する。
     *
     * @param length バイト数。不明なら -1（Length ヘッダを省略）。
     */
    public void put(String name, String mimeType, long length, InputStream data, ProgressListener listener)
            throws IOException {
        // 1 パケット目: 属性ヘッダのみ（Body を含めない方が相互運用性が高い）
        ByteArrayOutputStream h = new ByteArrayOutputStream();
        writeConnectionId(h);
        ObexHeaders.writeUnicode(h, ObexConstants.HI_NAME, name);
        if (mimeType != null && !mimeType.isEmpty()
                && h.size() + ObexHeaders.asciiSize(mimeType) + 8 <= maxPacket - 3) {
            ObexHeaders.writeAscii(h, ObexConstants.HI_TYPE, mimeType);
        }
        if (length >= 0 && length <= 0xFFFFFFFFL) {
            ObexHeaders.writeInt(h, ObexConstants.HI_LENGTH, length);
        }
        if (h.size() + 3 > maxPacket) throw new ObexException(ObexConstants.RSP_BAD_REQUEST, "ファイル名が長すぎます");
        sendAndExpect(ObexConstants.OP_PUT, h, ObexConstants.RSP_CONTINUE);

        int chunk = maxPacket - 3 - 3 - (connectionId >= 0 ? 5 : 0);
        byte[] buf = new byte[chunk];
        long sent = 0;
        while (true) {
            if (cancelled) {
                abort();
                throw new ObexException(ObexConstants.RSP_FORBIDDEN, "送信を中止しました");
            }
            int n = readFully(data, buf);
            if (n < chunk) {
                // 最後のチャンク: End-of-Body 付きの final PUT
                ByteArrayOutputStream b = new ByteArrayOutputStream(n + 8);
                writeConnectionId(b);
                ObexHeaders.writeBytes(b, ObexConstants.HI_END_OF_BODY, buf, 0, n);
                sent += n;
                sendAndExpect(ObexConstants.OP_PUT_FINAL, b, ObexConstants.RSP_OK);
                if (listener != null) listener.onProgress(sent, length);
                return;
            }
            ByteArrayOutputStream b = new ByteArrayOutputStream(n + 8);
            writeConnectionId(b);
            ObexHeaders.writeBytes(b, ObexConstants.HI_BODY, buf, 0, n);
            sendAndExpect(ObexConstants.OP_PUT, b, ObexConstants.RSP_CONTINUE);
            sent += n;
            if (listener != null) listener.onProgress(sent, length);
        }
    }

    public void disconnect() throws IOException {
        ByteArrayOutputStream h = new ByteArrayOutputStream();
        writeConnectionId(h);
        ObexPacket.write(out, ObexConstants.OP_DISCONNECT, h);
        readResponse();
    }

    private void abort() {
        try {
            ByteArrayOutputStream h = new ByteArrayOutputStream();
            writeConnectionId(h);
            ObexPacket.write(out, ObexConstants.OP_ABORT, h);
            readResponse();
        } catch (IOException ignored) {
            // 中止処理なので相手の応答は問わない
        }
    }

    private void sendAndExpect(int opcode, ByteArrayOutputStream body, int expected) throws IOException {
        ObexPacket.write(out, opcode, body);
        ObexPacket rsp = readResponse();
        if (rsp.code != expected) {
            throw new ObexException(rsp.code, "相手が受信を拒否しました");
        }
    }

    private ObexPacket readResponse() throws IOException {
        ObexPacket p = ObexPacket.read(in);
        if (p == null) throw new EOFException("相手が接続を切断しました");
        return p;
    }

    private void writeConnectionId(ByteArrayOutputStream b) {
        if (connectionId >= 0) ObexHeaders.writeInt(b, ObexConstants.HI_CONNECTION_ID, connectionId);
    }

    private static int readFully(InputStream in, byte[] buf) throws IOException {
        int off = 0;
        while (off < buf.length) {
            int n = in.read(buf, off, buf.length - off);
            if (n < 0) break;
            off += n;
        }
        return off;
    }
}
