package app.btfiletransfer.obex;

import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * OBEX パケット 1 つ分: opcode/レスポンスコード (1byte) + 全長 (2byte, big endian) + 本体。
 * CONNECT / SETPATH の固定フィールドの解釈は呼び出し側で行う。
 */
public final class ObexPacket {
    public final int code;
    /** opcode と長さフィールドを除いた本体。 */
    public final byte[] payload;

    public ObexPacket(int code, byte[] payload) {
        this.code = code;
        this.payload = payload;
    }

    /** 相手が接続を閉じた場合は null を返す。 */
    public static ObexPacket read(InputStream in) throws IOException {
        int code = in.read();
        if (code < 0) return null;
        int hi = in.read();
        int lo = in.read();
        if (hi < 0 || lo < 0) throw new EOFException("OBEX パケット途中で切断");
        int len = (hi << 8) | lo;
        if (len < 3) throw new ObexException(ObexConstants.RSP_BAD_REQUEST, "不正なパケット長 " + len);
        byte[] payload = new byte[len - 3];
        int off = 0;
        while (off < payload.length) {
            int n = in.read(payload, off, payload.length - off);
            if (n < 0) throw new EOFException("OBEX パケット途中で切断");
            off += n;
        }
        return new ObexPacket(code, payload);
    }

    public ObexHeaders headers(int fixedFieldLength) throws ObexException {
        if (payload.length < fixedFieldLength) {
            throw new ObexException(ObexConstants.RSP_BAD_REQUEST, "パケットが短すぎます");
        }
        return ObexHeaders.parse(payload, fixedFieldLength, payload.length);
    }

    /** code + 長さ + body を 1 回の write で送る（RFCOMM 上でパケットが分割されにくいように）。 */
    public static void write(OutputStream out, int code, ByteArrayOutputStream body) throws IOException {
        int len = 3 + (body == null ? 0 : body.size());
        if (len > 0xFFFF) throw new IllegalArgumentException("OBEX パケットが大きすぎます: " + len);
        ByteArrayOutputStream pkt = new ByteArrayOutputStream(len);
        pkt.write(code);
        pkt.write(len >> 8);
        pkt.write(len);
        if (body != null) body.writeTo(pkt);
        pkt.writeTo(out);
        out.flush();
    }

    /** CONNECT リクエスト／レスポンスの固定フィールド: version, flags, max packet length。 */
    public static ByteArrayOutputStream connectFields(int maxPacket) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        b.write(ObexConstants.OBEX_VERSION);
        b.write(0);
        b.write(maxPacket >> 8);
        b.write(maxPacket);
        return b;
    }

    /** CONNECT の固定フィールドから相手の最大パケット長を取り出す。 */
    public int connectMaxPacket() throws ObexException {
        if (payload.length < 4) throw new ObexException(ObexConstants.RSP_BAD_REQUEST, "CONNECT が短すぎます");
        return ((payload[2] & 0xFF) << 8) | (payload[3] & 0xFF);
    }
}
