package app.btfiletransfer.obex;

import java.util.UUID;

/** OBEX 1.x の定数（IrDA OBEX 仕様 / Bluetooth GOEP・OPP で使用する範囲のみ）。 */
public final class ObexConstants {
    private ObexConstants() {}

    /** Bluetooth OBEX Object Push Profile のサービスクラス UUID (0x1105)。 */
    public static final UUID OPP_UUID = UUID.fromString("00001105-0000-1000-8000-00805F9B34FB");

    public static final int OBEX_VERSION = 0x10;
    /** OBEX パケット長フィールドの上限 (0xFFFF) 未満で、Android 標準実装と同じ値。 */
    public static final int MAX_PACKET_SIZE = 0xFFFE;
    /** 仕様上の最小パケット長。 */
    public static final int MIN_PACKET_SIZE = 255;

    public static final int FINAL_BIT = 0x80;

    // リクエスト opcode
    public static final int OP_CONNECT = 0x80;
    public static final int OP_DISCONNECT = 0x81;
    public static final int OP_PUT = 0x02;
    public static final int OP_PUT_FINAL = 0x82;
    public static final int OP_GET = 0x03;
    public static final int OP_GET_FINAL = 0x83;
    public static final int OP_SETPATH = 0x85;
    public static final int OP_ABORT = 0xFF;

    // レスポンスコード（final bit 付き）
    public static final int RSP_CONTINUE = 0x90;
    public static final int RSP_OK = 0xA0;
    public static final int RSP_BAD_REQUEST = 0xC0;
    public static final int RSP_FORBIDDEN = 0xC3;
    public static final int RSP_NOT_FOUND = 0xC4;
    public static final int RSP_ENTITY_TOO_LARGE = 0xCD;
    public static final int RSP_INTERNAL_ERROR = 0xD0;
    public static final int RSP_NOT_IMPLEMENTED = 0xD1;
    public static final int RSP_SERVICE_UNAVAILABLE = 0xD3;

    // ヘッダ ID（上位2bitがエンコード種別: 00=Unicode, 01=バイト列, 10=1byte, 11=4byte）
    public static final int HI_NAME = 0x01;
    public static final int HI_TYPE = 0x42;
    public static final int HI_LENGTH = 0xC3;
    public static final int HI_BODY = 0x48;
    public static final int HI_END_OF_BODY = 0x49;
    public static final int HI_TARGET = 0x46;
    public static final int HI_CONNECTION_ID = 0xCB;
}
