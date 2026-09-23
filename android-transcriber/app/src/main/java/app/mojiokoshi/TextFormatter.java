package app.mojiokoshi;

import java.util.regex.Pattern;

/** Vosk の出力をコピペしやすい普通の日本語テキストに整える。 */
final class TextFormatter {
    /**
     * Vosk の日本語モデルは単語ごとに半角スペースで区切って返す (例: "今日 は 晴れ")。
     * 日本語の文字どうしの間のスペースだけを取り除き、英単語間のスペースは残す。
     */
    private static final Pattern SPACE_BETWEEN_NON_ASCII =
            Pattern.compile("(?<=[^\\x00-\\x7F])\\s+|\\s+(?=[^\\x00-\\x7F])");

    private TextFormatter() {
    }

    static String clean(String raw) {
        if (raw == null) {
            return "";
        }
        return SPACE_BETWEEN_NON_ASCII.matcher(raw.trim()).replaceAll("");
    }
}
