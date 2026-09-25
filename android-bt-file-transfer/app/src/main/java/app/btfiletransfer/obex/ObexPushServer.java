package app.btfiletransfer.obex;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * OBEX Object Push サーバの 1 セッション分。Windows の「ファイルを送信する」(fsquirt) から受け取る側。
 * {@link #run()} は相手が DISCONNECT するか接続が切れるまでブロックする。
 */
public final class ObexPushServer {

    /** 受信ファイルの書き込み先。 */
    public interface Sink {
        void write(byte[] b, int off, int len) throws IOException;

        void commit() throws IOException;

        void abort();
    }

    public interface Handler {
        /**
         * PUT の開始時に呼ばれる。拒否する場合は {@link ObexException} を投げる（そのレスポンスコードを返す）。
         *
         * @param name   相手が送ってきたファイル名（未サニタイズ・null の可能性あり）
         * @param length 宣言されたサイズ。不明なら -1
         */
        Sink onPutStart(String name, String mimeType, long length) throws IOException;
    }

    private final InputStream in;
    private final OutputStream out;
    private final Handler handler;
    private final int localMaxPacket;

    // 進行中の PUT
    private boolean inPut;
    private String putName;
    private String putType;
    private long putLength = -1;
    private Sink sink;

    public ObexPushServer(InputStream in, OutputStream out, Handler handler) {
        this(in, out, handler, ObexConstants.MAX_PACKET_SIZE);
    }

    public ObexPushServer(InputStream in, OutputStream out, Handler handler, int localMaxPacket) {
        this.in = in;
        this.out = out;
        this.handler = handler;
        this.localMaxPacket = localMaxPacket;
    }

    public void run() throws IOException {
        try {
            while (true) {
                ObexPacket req = ObexPacket.read(in);
                if (req == null) return;
                switch (req.code) {
                    case ObexConstants.OP_CONNECT:
                        onConnect(req);
                        break;
                    case ObexConstants.OP_DISCONNECT:
                        resetPut();
                        respond(ObexConstants.RSP_OK);
                        return;
                    case ObexConstants.OP_PUT:
                    case ObexConstants.OP_PUT_FINAL:
                        onPut(req);
                        break;
                    case ObexConstants.OP_ABORT:
                        resetPut();
                        respond(ObexConstants.RSP_OK);
                        break;
                    default:
                        // GET（名刺の取得）や SETPATH は OPP 受信には不要
                        resetPut();
                        respond(ObexConstants.RSP_NOT_IMPLEMENTED);
                        break;
                }
            }
        } finally {
            resetPut();
        }
    }

    private void onConnect(ObexPacket req) throws IOException {
        req.connectMaxPacket();
        ObexHeaders h = req.headers(4);
        if (h.target != null) {
            // Target 付きはフォルダブラウジング(FTP)等。OPP では受け付けない
            respond(ObexConstants.RSP_SERVICE_UNAVAILABLE);
            return;
        }
        ObexPacket.write(out, ObexConstants.RSP_OK, ObexPacket.connectFields(localMaxPacket));
    }

    private void onPut(ObexPacket req) throws IOException {
        boolean isFinal = req.code == ObexConstants.OP_PUT_FINAL;
        ObexHeaders h;
        try {
            h = req.headers(0);
        } catch (ObexException e) {
            resetPut();
            respond(e.responseCode);
            return;
        }
        if (!inPut) {
            inPut = true;
            putName = null;
            putType = null;
            putLength = -1;
        }
        if (h.name != null) putName = h.name;
        if (h.type != null) putType = h.type;
        if (h.length >= 0) putLength = h.length;

        try {
            if (h.body != null) {
                if (sink == null) sink = handler.onPutStart(putName, putType, putLength);
                if (h.body.length > 0) sink.write(h.body, 0, h.body.length);
            }
            if (!isFinal) {
                respond(ObexConstants.RSP_CONTINUE);
                return;
            }
            if (sink == null) {
                // Body を一度も含まない PUT は OBEX 上「削除」要求。OPP では受け付けない
                resetPut();
                respond(ObexConstants.RSP_FORBIDDEN);
                return;
            }
            Sink done = sink;
            sink = null;
            inPut = false;
            done.commit();
            respond(ObexConstants.RSP_OK);
        } catch (ObexException e) {
            resetPut();
            respond(e.responseCode);
        } catch (IOException e) {
            // 保存先への書き込み失敗。通信路の失敗なら respond() も失敗して run() から抜ける
            resetPut();
            respond(ObexConstants.RSP_INTERNAL_ERROR);
        }
    }

    private void resetPut() {
        inPut = false;
        if (sink != null) {
            sink.abort();
            sink = null;
        }
    }

    private void respond(int code) throws IOException {
        ObexPacket.write(out, code, (ByteArrayOutputStream) null);
    }
}
