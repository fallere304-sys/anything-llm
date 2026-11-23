package com.z4motioncam;

/** Explains the "install apps from unknown sources" switch for the Android version in use. */
final class InstallGuide {
    private InstallGuide() {}

    /**
     * @param sdk              Build.VERSION.SDK_INT
     * @param allowedBySetting Android 7.1 and older: the global "unknown sources" switch is on
     * @param canRequest       Android 8+: this app may install packages (PackageManager.canRequestPackageInstalls)
     * @return null when nothing is needed, otherwise what to do (Japanese)
     */
    static String advice(int sdk, boolean allowedBySetting, boolean canRequest) {
        if (sdk >= 26) {
            return canRequest ? null
                    : "このアプリに更新の導入を許可してください：設定 > アプリ > Z4 MotionCam > 「不明なアプリのインストール」をオン";
        }
        return allowedBySetting ? null
                : "「提供元不明のアプリ」を許可してください：設定 > セキュリティ > 提供元不明のアプリ をオン（導入が終わったらオフに戻せます）";
    }
}
