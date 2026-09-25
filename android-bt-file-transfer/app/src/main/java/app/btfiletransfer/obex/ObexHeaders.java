package app.btfiletransfer.obex;

import java.io.ByteArrayOutputStream;
import java.nio.charset.Charset;

/**
 * OBEX ヘッダ集合。OPP で必要なヘッダだけを型付きで保持し、それ以外は読み飛ばす。
 */
public final class ObexHeaders {
    private static final Charset UTF_16BE = Charset.forName("UTF-16BE");
    private static final Charset US_ASCII = Charset.forName("US-ASCII");

    public String name;
    public String type;
    /** Length ヘッダ。未指定は -1。 */
    public long length = -1;
    /** Body もしくは End-of-Body の内容。無ければ null。 */
    public byte[] body;
    public boolean endOfBody;
    public byte[] target;
    /** Connection ID。未指定は -1。 */
    public long connectionId = -1;

    /** buf[offset, end) をヘッダ列として解析する。 */
    public static ObexHeaders parse(byte[] buf, int offset, int end) throws ObexException {
        ObexHeaders h = new ObexHeaders();
        int p = offset;
        while (p < end) {
            int hi = buf[p] & 0xFF;
            switch (hi & 0xC0) {
                case 0x00:
                case 0x40: {
                    if (p + 3 > end) throw malformed();
                    int len = ((buf[p + 1] & 0xFF) << 8) | (buf[p + 2] & 0xFF);
                    if (len < 3 || p + len > end) throw malformed();
                    int vs = p + 3;
                    int vl = len - 3;
                    if ((hi & 0xC0) == 0x00) {
                        String s = decodeUnicode(buf, vs, vl);
                        if (hi == ObexConstants.HI_NAME) h.name = s;
                    } else if (hi == ObexConstants.HI_TYPE) {
                        h.type = decodeAscii(buf, vs, vl);
                    } else if (hi == ObexConstants.HI_BODY || hi == ObexConstants.HI_END_OF_BODY) {
                        h.body = copy(buf, vs, vl);
                        if (hi == ObexConstants.HI_END_OF_BODY) h.endOfBody = true;
                    } else if (hi == ObexConstants.HI_TARGET) {
                        h.target = copy(buf, vs, vl);
                    }
                    p += len;
                    break;
                }
                case 0x80:
                    if (p + 2 > end) throw malformed();
                    p += 2;
                    break;
                default: {
                    if (p + 5 > end) throw malformed();
                    long v = ((long) (buf[p + 1] & 0xFF) << 24) | ((buf[p + 2] & 0xFF) << 16)
                            | ((buf[p + 3] & 0xFF) << 8) | (buf[p + 4] & 0xFF);
                    if (hi == ObexConstants.HI_LENGTH) h.length = v;
                    else if (hi == ObexConstants.HI_CONNECTION_ID) h.connectionId = v;
                    p += 5;
                    break;
                }
            }
        }
        return h;
    }

    // ---- エンコード ----

    public static void writeUnicode(ByteArrayOutputStream out, int hi, String value) {
        byte[] v = value.isEmpty() ? new byte[0] : (value + '\0').getBytes(UTF_16BE);
        writeBytes(out, hi, v, 0, v.length);
    }

    public static void writeAscii(ByteArrayOutputStream out, int hi, String value) {
        byte[] s = value.getBytes(US_ASCII);
        byte[] v = new byte[s.length + 1];
        System.arraycopy(s, 0, v, 0, s.length);
        writeBytes(out, hi, v, 0, v.length);
    }

    public static void writeBytes(ByteArrayOutputStream out, int hi, byte[] v, int off, int len) {
        int total = len + 3;
        out.write(hi);
        out.write(total >> 8);
        out.write(total);
        out.write(v, off, len);
    }

    public static void writeInt(ByteArrayOutputStream out, int hi, long value) {
        out.write(hi);
        out.write((int) (value >> 24));
        out.write((int) (value >> 16));
        out.write((int) (value >> 8));
        out.write((int) value);
    }

    /** Unicode テキストヘッダのエンコード後サイズ。 */
    public static int unicodeSize(String value) {
        return 3 + (value.isEmpty() ? 0 : (value.length() + 1) * 2);
    }

    public static int asciiSize(String value) {
        return 3 + value.getBytes(US_ASCII).length + 1;
    }

    private static String decodeUnicode(byte[] buf, int off, int len) {
        if (len >= 2 && buf[off + len - 2] == 0 && buf[off + len - 1] == 0) len -= 2;
        return new String(buf, off, len & ~1, UTF_16BE);
    }

    private static String decodeAscii(byte[] buf, int off, int len) {
        while (len > 0 && buf[off + len - 1] == 0) len--;
        // 実際には UTF-8 を送ってくる実装もあるので寛容に扱う
        return new String(buf, off, len, Charset.forName("UTF-8"));
    }

    private static byte[] copy(byte[] buf, int off, int len) {
        byte[] r = new byte[len];
        System.arraycopy(buf, off, r, 0, len);
        return r;
    }

    private static ObexException malformed() {
        return new ObexException(ObexConstants.RSP_BAD_REQUEST, "壊れた OBEX ヘッダ");
    }
}
