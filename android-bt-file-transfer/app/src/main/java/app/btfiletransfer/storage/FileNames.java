package app.btfiletransfer.storage;

/** 相手から受け取ったファイル名を安全な保存名に変換する（純 Java、単体テスト対象）。 */
public final class FileNames {
    private FileNames() {}

    static final int MAX_LENGTH = 120;

    /**
     * パス区切り・制御文字・FAT/NTFS で使えない文字を除去し、ディレクトリトラバーサルを防ぐ。
     *
     * @param fallback 名前が空になった場合に使う名前
     */
    public static String sanitize(String name, String fallback) {
        if (name == null) return fallback;
        // Windows/Unix どちらの区切りでもパス部分を捨てる
        int slash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        String base = name.substring(slash + 1);

        StringBuilder sb = new StringBuilder(base.length());
        for (int i = 0; i < base.length(); i++) {
            char c = base.charAt(i);
            if (c < 0x20 || c == 0x7F || "\"*:<>?|".indexOf(c) >= 0) {
                sb.append('_');
            } else {
                sb.append(c);
            }
        }
        String s = sb.toString().trim();
        // 先頭のドットは隠しファイル化・".." になるので除去
        while (s.startsWith(".")) s = s.substring(1);
        if (s.isEmpty()) return fallback;
        if (s.length() > MAX_LENGTH) {
            int dot = s.lastIndexOf('.');
            String ext = (dot > 0 && s.length() - dot <= 16) ? s.substring(dot) : "";
            s = s.substring(0, MAX_LENGTH - ext.length()) + ext;
            // サロゲートペアの途中で切った場合の後始末
            s = s.replaceAll("[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])", "");
        }
        return s;
    }

    /** "name.ext" → "name (n).ext" */
    public static String numbered(String name, int n) {
        int dot = name.lastIndexOf('.');
        if (dot <= 0) return name + " (" + n + ")";
        return name.substring(0, dot) + " (" + n + ")" + name.substring(dot);
    }
}
