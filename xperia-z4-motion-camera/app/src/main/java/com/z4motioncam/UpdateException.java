package com.z4motioncam;

import java.io.IOException;

/** A failed update step, with a sentence for the person (Japanese) and a machine-readable kind. */
final class UpdateException extends IOException {
    enum Kind {
        NOT_HTTPS, NETWORK, BAD_MANIFEST, PIN_MISMATCH, STORAGE, TOO_LARGE, SHA_MISMATCH, BAD_FORMAT,
        PACKAGE_MISMATCH, VERSION_MISMATCH, SIGNATURE_MISMATCH, SIGNATURE_UNREADABLE, CANCELLED, NOTHING_READY
    }

    final Kind kind;
    /** Shown on the status screen and in the browser. */
    final String userMessage;

    UpdateException(Kind kind, String userMessage) {
        this(kind, userMessage, null);
    }

    UpdateException(Kind kind, String userMessage, Throwable cause) {
        super(userMessage, cause);
        this.kind = kind;
        this.userMessage = userMessage;
    }

    /** Network failures are worth retrying later; everything else needs a change on the server side. */
    boolean retryable() {
        return kind == Kind.NETWORK || kind == Kind.STORAGE;
    }
}
