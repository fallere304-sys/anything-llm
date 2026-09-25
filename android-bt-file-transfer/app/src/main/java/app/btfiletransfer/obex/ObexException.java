package app.btfiletransfer.obex;

import java.io.IOException;

/** OBEX レベルのエラー。{@link #responseCode} は相手に返す／相手から返されたレスポンスコード。 */
public class ObexException extends IOException {
    public final int responseCode;

    public ObexException(int responseCode, String message) {
        super(message + String.format(" (OBEX 0x%02X)", responseCode));
        this.responseCode = responseCode;
    }
}
