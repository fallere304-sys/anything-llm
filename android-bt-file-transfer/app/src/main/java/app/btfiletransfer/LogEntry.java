package app.btfiletransfer;

import android.net.Uri;

/** 画面の履歴リストに出す 1 行。 */
public final class LogEntry {
    public final long time = System.currentTimeMillis();
    public final String text;
    /** 受信ファイル等、タップで開ける対象。無ければ null。 */
    public final Uri uri;
    public final String mimeType;
    public final boolean error;

    public LogEntry(String text, Uri uri, String mimeType, boolean error) {
        this.text = text;
        this.uri = uri;
        this.mimeType = mimeType;
        this.error = error;
    }
}
