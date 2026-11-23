package com.z4motioncam;

import java.io.File;
import java.io.IOException;
import java.util.Locale;
import java.util.Set;

/**
 * Pull-style update: fetch the manifest, compare versions, download the APK (resumable), and
 * verify it completely before anything is offered for installation. Android specifics (installed
 * version, signing certificates, reading an APK's package info) come in through {@link Env}, so the
 * whole flow runs against a local server in the unit tests.
 *
 * <p>Layers of protection, in the order they apply:
 * <ol>
 *   <li>https only, platform certificate and host name checks (every redirect hop too);
 *   <li>optional SHA-256 pinned by the owner through another channel must equal the manifest's;
 *   <li>size and SHA-256 of the downloaded file must equal the manifest's;
 *   <li>the file must be a real APK of this package, with the manifest's versionCode, newer than the
 *       installed one;
 *   <li>its signing certificates must equal the installed app's (otherwise Android would refuse the
 *       update anyway; this explains why, before the installer screen);
 *   <li>after all that Android's own installer asks the person for the final confirmation.
 * </ol>
 */
final class UpdateEngine {
    /** What the engine needs to know about the device. */
    interface Env {
        String packageName();

        int installedVersionCode();

        long wallMs();

        /** Lowercase hex SHA-256 of each signing certificate of the installed app. */
        Set<String> installedSigners();

        /** Package info of an APK file (name, versionCode, signers), or null if it cannot be read. */
        ApkInfo readApk(File apk);
    }

    static final class ApkInfo {
        final String pkg;
        final int versionCode;
        final Set<String> signers;

        ApkInfo(String pkg, int versionCode, Set<String> signers) {
            this.pkg = pkg;
            this.versionCode = versionCode;
            this.signers = signers;
        }
    }

    /** Extra room kept free beyond the APK itself (the installer needs working space too). */
    static final long STORAGE_MARGIN = 20L * 1024 * 1024;

    private final UpdateStore store;
    private final Env env;
    private final UpdateHttp http;

    UpdateEngine(UpdateStore store, Env env, UpdateHttp http) {
        this.store = store;
        this.env = env;
        this.http = http;
    }

    UpdateStore store() {
        return store;
    }

    /**
     * Reads the manifest.
     *
     * @param pinnedSha optional SHA-256 the owner obtained separately; empty = none
     * @return the manifest when it describes a newer version, null when this version is current
     */
    UpdateManifest check(String manifestUrl, String pinnedSha) throws UpdateException {
        byte[] json = http.getBytes(manifestUrl, UpdateManifest.MAX_JSON_BYTES);
        UpdateManifest m = UpdateManifest.parse(json, manifestUrl);
        if (!m.pkg.equals(env.packageName())) {
            throw new UpdateException(UpdateException.Kind.PACKAGE_MISMATCH, "別のアプリの更新情報です");
        }
        if (m.versionCode <= env.installedVersionCode()) return null;
        String pin = pinnedSha == null ? "" : pinnedSha.trim().toLowerCase(Locale.US);
        if (!pin.isEmpty() && !pin.equals(m.sha256)) {
            throw new UpdateException(UpdateException.Kind.PIN_MISMATCH,
                    "更新情報の SHA-256 が、設定した値と一致しません（取得元が差し替えられたか、設定の値が古い可能性があります）");
        }
        return m;
    }

    /**
     * Downloads (or continues) and fully verifies the update. On return the file is ready to hand
     * to the installer. A broken or foreign file is deleted; a merely interrupted download is kept.
     */
    File download(UpdateManifest m, UpdateHttp.Progress progress) throws UpdateException {
        if (m.versionCode <= env.installedVersionCode()) {
            throw new UpdateException(UpdateException.Kind.VERSION_MISMATCH, "すでにこの版か、より新しい版が入っています");
        }
        File ready = store.ready(m.versionCode);
        if (ready.isFile() && matchesMeta(m)) {
            try {
                verifyFile(ready, m); // downloaded before: still good?
                verifyIdentity(ready, m);
                return ready;
            } catch (UpdateException e) {
                store.deleteVersion(m.versionCode);
            }
        }
        File part = store.part(m.versionCode);
        UpdateStore.Meta meta = store.readMeta(m.versionCode);
        if (meta == null || !meta.matches(m)) {
            //noinspection ResultOfMethodCallIgnored
            part.delete(); // a different file than the one that was started: do not continue it
            //noinspection ResultOfMethodCallIgnored
            ready.delete();
        }
        long have = part.isFile() ? part.length() : 0;
        long need = Math.max(0, m.size - have) + STORAGE_MARGIN;
        if (store.usableBytes() < need) {
            throw new UpdateException(UpdateException.Kind.STORAGE, "空き容量が足りません（約 " + (need / (1024 * 1024))
                    + " MB 必要、空き " + (store.usableBytes() / (1024 * 1024)) + " MB）。録画を整理してください");
        }
        try {
            store.writeMeta(m.versionCode, m);
        } catch (IOException e) {
            throw new UpdateException(UpdateException.Kind.STORAGE, "更新用の保存先に書き込めません", e);
        }
        try {
            http.download(m.apkUrl, part, m.size, progress);
        } catch (UpdateException e) {
            if (e.kind == UpdateException.Kind.TOO_LARGE) store.deleteVersion(m.versionCode);
            throw e; // network errors keep the part file for a later resume
        }
        File target = store.ready(m.versionCode);
        try {
            verifyFile(part, m);
        } catch (UpdateException e) {
            store.deleteVersion(m.versionCode);
            throw e;
        }
        //noinspection ResultOfMethodCallIgnored
        target.delete();
        if (!part.renameTo(target)) {
            store.deleteVersion(m.versionCode);
            throw new UpdateException(UpdateException.Kind.STORAGE, "ダウンロードしたファイルを保存できません");
        }
        try {
            verifyIdentity(target, m);
        } catch (UpdateException e) {
            store.deleteVersion(m.versionCode);
            throw e;
        }
        return target;
    }

    private boolean matchesMeta(UpdateManifest m) {
        UpdateStore.Meta meta = store.readMeta(m.versionCode);
        return meta != null && meta.matches(m);
    }

    /**
     * Re-verifies a downloaded update right before it is handed to the installer, from the values
     * saved with it (the file may have been tampered with or damaged since the download).
     */
    File verifyReady(int versionCode) throws UpdateException {
        File ready = store.ready(versionCode);
        UpdateStore.Meta meta = store.readMeta(versionCode);
        if (!ready.isFile() || meta == null) {
            throw new UpdateException(UpdateException.Kind.NOTHING_READY, "導入できる更新がありません。先にダウンロードしてください");
        }
        try {
            ApkCheck.checkFormat(ready, meta.size);
            String sha = ApkCheck.sha256Hex(ready);
            if (!sha.equals(meta.sha256)) {
                throw new UpdateException(UpdateException.Kind.SHA_MISMATCH, "保存した更新ファイルが変わっています（SHA-256 が一致しません）");
            }
            verifyIdentity(ready, versionCode);
        } catch (IOException e) {
            if (e instanceof UpdateException) {
                store.deleteVersion(versionCode);
                throw (UpdateException) e;
            }
            throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "更新ファイルを読めません", e);
        }
        return ready;
    }

    private void verifyFile(File f, UpdateManifest m) throws UpdateException {
        ApkCheck.checkFormat(f, m.size);
        String sha;
        try {
            sha = ApkCheck.sha256Hex(f);
        } catch (IOException e) {
            throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "ダウンロードしたファイルを読めません", e);
        }
        if (!sha.equals(m.sha256)) {
            throw new UpdateException(UpdateException.Kind.SHA_MISMATCH,
                    "ダウンロードしたファイルの SHA-256 が更新情報と一致しません（通信の途中や取得元で内容が変わった可能性があります）。破棄しました");
        }
    }

    private void verifyIdentity(File apk, UpdateManifest m) throws UpdateException {
        verifyIdentity(apk, m.versionCode);
    }

    private void verifyIdentity(File apk, int expectedVersionCode) throws UpdateException {
        ApkInfo info = env.readApk(apk);
        if (info == null) {
            throw new UpdateException(UpdateException.Kind.BAD_FORMAT, "APK の情報を読み取れません（壊れているか、この端末では読めない形式です）");
        }
        if (!env.packageName().equals(info.pkg)) {
            throw new UpdateException(UpdateException.Kind.PACKAGE_MISMATCH, "別のアプリの APK です（" + info.pkg + "）。導入しません");
        }
        if (info.versionCode != expectedVersionCode) {
            throw new UpdateException(UpdateException.Kind.VERSION_MISMATCH,
                    "APK の版（" + info.versionCode + "）が更新情報（" + expectedVersionCode + "）と違います。導入しません");
        }
        if (info.versionCode <= env.installedVersionCode()) {
            throw new UpdateException(UpdateException.Kind.VERSION_MISMATCH, "今入っている版以下の APK です。導入しません");
        }
        if (info.signers == null || info.signers.isEmpty()) {
            throw new UpdateException(UpdateException.Kind.SIGNATURE_UNREADABLE, "APK の署名を確認できません。導入しません");
        }
        if (!ApkCheck.sameSigners(env.installedSigners(), info.signers)) {
            throw new UpdateException(UpdateException.Kind.SIGNATURE_MISMATCH,
                    "署名が今のアプリと違うため、上書きの更新はできません。別の鍵で署名された版です。"
                            + "入れ替えるには一度アンインストールが必要で、保存した録画も消えます（先に録画を退避してください）");
        }
    }
}
