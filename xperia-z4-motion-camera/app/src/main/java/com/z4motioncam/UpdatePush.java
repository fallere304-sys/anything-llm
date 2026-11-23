package com.z4motioncam;

import java.io.InputStream;

/**
 * The "send it from the browser" entrance of the update. A person at the phone opens a short
 * window ({@link UpdateWindow}: a six-digit code shown only on the phone's screen); the browser
 * sends the APK together with that code; the body is streamed into {@link UpdateEngine#receive},
 * which checks it like any other update and stores it as the ready one. Everything after that
 * (re-verification, installer, clean-up) is the pull flow's. No Android classes: unit-tested over
 * real sockets.
 */
final class UpdatePush {
    /** What {@link UpdateManager} tells this class about what else is going on. */
    interface Gate {
        /** @return null when an upload may start (and the caller is now busy), else a reason in Japanese */
        String enter();

        /** The upload finished; {@code received} is the update that is now ready, or null when it failed. */
        void leave(UpdateManifest received);
    }

    static final class Result {
        /** HTTP status to answer with. */
        final int http;
        final boolean ok;
        final String message;
        final String sha256;
        final long size;
        final String versionName;

        Result(int http, boolean ok, String message, String sha256, long size, String versionName) {
            this.http = http;
            this.ok = ok;
            this.message = message;
            this.sha256 = sha256 == null ? "" : sha256;
            this.size = size;
            this.versionName = versionName == null ? "" : versionName;
        }

        String json() {
            return "{\"ok\":" + ok + ",\"message\":" + HttpServer.jsonString(message)
                    + ",\"sha256\":" + HttpServer.jsonString(sha256) + ",\"size\":" + size
                    + ",\"version\":" + HttpServer.jsonString(versionName) + "}";
        }

        static Result fail(int http, String message) {
            return new Result(http, false, message, "", 0, "");
        }
    }

    private final UpdateEngine engine;
    private final UpdateWindow window;
    private final Gate gate;

    UpdatePush(UpdateEngine engine, UpdateWindow window, Gate gate) {
        this.engine = engine;
        this.window = window;
        this.gate = gate;
    }

    UpdateWindow window() {
        return window;
    }

    /**
     * Handles an upload. The body is read only after the code was accepted.
     *
     * @param length the declared Content-Length (required)
     */
    Result receive(String code, InputStream body, long length, long now) {
        switch (window.check(code, now)) {
            case CLOSED:
                return Result.fail(403, "更新の受け付け中ではありません。Z4 の画面の「更新」ボタンから「ブラウザから送る」を選び、表示されたコードを入力してください");
            case WRONG:
                return Result.fail(403, "コードが違います（あと" + window.wrongLeft() + "回まで）。Z4 の画面に表示されている6桁のコードを入力してください");
            case LOCKED:
                return Result.fail(403, "コードを続けて間違えたため、受け付けを終了しました。Z4 でもう一度「ブラウザから送る」を選んでください");
            default:
                break;
        }
        if (length <= 0) return Result.fail(400, "ファイルが空です");
        if (length > UpdateEngine.PUSH_MAX_BYTES) {
            return Result.fail(413, "ファイルが大きすぎます（上限 " + UpdateEngine.PUSH_MAX_BYTES / 1024 / 1024 + "MB）");
        }
        String busy = gate.enter();
        if (busy != null) return Result.fail(409, busy);
        UpdateManifest got = null;
        try {
            got = engine.receive(body, length);
            window.close(); // the code is single-use
            return new Result(200, true, "検証できました（同じ署名の APK です）", got.sha256, got.size, got.versionName);
        } catch (UpdateException e) {
            int http = e.kind == UpdateException.Kind.STORAGE ? 500 : e.kind == UpdateException.Kind.TOO_LARGE ? 413 : 400;
            return Result.fail(http, e.userMessage);
        } catch (RuntimeException e) {
            return Result.fail(500, "受信中に問題が起きました（" + e.getClass().getSimpleName() + "）");
        } finally {
            gate.leave(got);
        }
    }
}
