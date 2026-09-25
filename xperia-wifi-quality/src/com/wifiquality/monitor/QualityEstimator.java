package com.wifiquality.monitor;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/**
 * 推定通信品質の計算（Android API に依存しない純粋な Java）。
 *
 * 推定品質[%] = 信号係数 × 混線係数 × 100
 *
 * 信号係数: IEEE 802.11n/ac の受信最小感度（20MHz, 1ストリーム）を基に、
 *   RSSI で到達できる MCS の伝送レートを MCS9 (86.7Mbps) で正規化した値。
 *   チャネル幅が 2 倍になるごとに必要感度が 3dB 上がるため補正する。
 *   → 至近距離（-57dBm 以上 @20MHz）で 1.0。
 *
 * 混線係数: 周波数が重なる他の電波ごとに
 *   負荷 = 重なり率(0〜1) × 強度係数(-90dBm で 0, -62dBm 以上で 1)
 *   を合計し、1 / (1 + 0.5 × 負荷合計) とする。
 *   （同じ強さの同一ch の電波が 1 つあると 67%、3 つで 40%）
 */
public final class QualityEstimator {

    /** 1 つの BSSID（電波）の観測値。 */
    public static final class Ap {
        public final String ssid;
        public final String bssid;
        public final int frequency;   // プライマリチャネルの中心周波数 [MHz]
        public final int centerFreq0; // 40/80/160MHz 時の全体中心周波数 [MHz]（不明なら 0）
        public final int widthMhz;    // 20 / 40 / 80 / 160
        public final int rssi;        // [dBm]

        // 計算結果
        public int channel;
        public String band;
        public int coChannelCount;    // 同一プライマリチャネルの他の電波数
        public int overlapCount;      // 部分的に重なる他の電波数
        public double interferenceLoad;
        public double signalFactor;
        public double interferenceFactor;
        public int qualityPercent;

        public Ap(String ssid, String bssid, int frequency, int centerFreq0, int widthMhz, int rssi) {
            this.ssid = ssid;
            this.bssid = bssid;
            this.frequency = frequency;
            this.centerFreq0 = centerFreq0;
            this.widthMhz = widthMhz <= 0 ? 20 : widthMhz;
            this.rssi = rssi;
        }

        boolean is24GHz() {
            return frequency < 3000;
        }

        /** プライマリ 20MHz チャネルの占有帯域 [low, high]。2.4GHz は DSSS 互換の 22MHz 幅で扱う。 */
        double[] primaryRange() {
            double half = is24GHz() ? 11 : 10;
            return new double[] {frequency - half, frequency + half};
        }

        /** チャネル全体（40/80/160MHz 含む）の占有帯域 [low, high]。 */
        double[] fullRange() {
            if (widthMhz <= 20) {
                return primaryRange();
            }
            double center = centerFreq0 > 0 ? centerFreq0 : frequency;
            return new double[] {center - widthMhz / 2.0, center + widthMhz / 2.0};
        }
    }

    // RSSI [dBm] → 信号係数 の対応点（802.11 受信最小感度と MCS レート / 86.7Mbps）
    private static final double[][] SIGNAL_TABLE = {
        {-57, 1.000}, // MCS9 256QAM 5/6
        {-59, 0.900}, // MCS8 256QAM 3/4
        {-64, 0.750}, // MCS7 64QAM 5/6
        {-65, 0.675}, // MCS6 64QAM 3/4
        {-66, 0.600}, // MCS5 64QAM 2/3
        {-70, 0.450}, // MCS4 16QAM 3/4
        {-74, 0.300}, // MCS3 16QAM 1/2
        {-77, 0.225}, // MCS2 QPSK 3/4
        {-79, 0.150}, // MCS1 QPSK 1/2
        {-82, 0.075}, // MCS0 BPSK 1/2
        {-90, 0.000},
    };

    private static final double INTERFERER_FULL_DBM = -62; // エネルギー検出閾値。これ以上は最大の影響
    private static final double INTERFERER_NONE_DBM = -90; // これ以下は影響なしとみなす
    private static final double LOAD_WEIGHT = 0.5;         // 他 BSS の平均的な送信占有率の仮定

    private QualityEstimator() {}

    public static int frequencyToChannel(int freq) {
        if (freq == 2484) return 14;
        if (freq >= 2412 && freq < 2484) return (freq - 2407) / 5;
        if (freq >= 4910 && freq <= 4980) return (freq - 4000) / 5;
        if (freq >= 5000 && freq <= 5900) return (freq - 5000) / 5;
        if (freq >= 5955 && freq <= 7115) return (freq - 5950) / 5; // 6GHz
        return 0;
    }

    public static String frequencyToBand(int freq) {
        if (freq < 3000) return "2.4GHz";
        if (freq < 5925) return "5GHz";
        return "6GHz";
    }

    public static double signalFactor(int rssi, int widthMhz) {
        // 幅が 2 倍になるごとに必要感度が 3dB 厳しくなる
        double widthPenalty = 3.0 * (Math.log(Math.max(widthMhz, 20) / 20.0) / Math.log(2));
        double r = rssi - widthPenalty;
        if (r >= SIGNAL_TABLE[0][0]) return SIGNAL_TABLE[0][1];
        for (int i = 1; i < SIGNAL_TABLE.length; i++) {
            double[] hi = SIGNAL_TABLE[i - 1];
            double[] lo = SIGNAL_TABLE[i];
            if (r >= lo[0]) {
                double t = (r - lo[0]) / (hi[0] - lo[0]);
                return lo[1] + t * (hi[1] - lo[1]);
            }
        }
        return 0.0;
    }

    public static double interfererStrength(int rssi) {
        double s = (rssi - INTERFERER_NONE_DBM) / (INTERFERER_FULL_DBM - INTERFERER_NONE_DBM);
        return Math.max(0.0, Math.min(1.0, s));
    }

    private static double overlapLength(double[] a, double[] b) {
        return Math.max(0.0, Math.min(a[1], b[1]) - Math.max(a[0], b[0]));
    }

    /**
     * 電波 b が電波 a に与える重なり率 (0〜1)。
     * a のプライマリチャネルに重なる場合は送信待ち（帯域共有）が発生するため、
     * プライマリへの重なり率と全帯域への重なり率の大きい方を採用する。
     */
    public static double overlapRatio(Ap a, Ap b) {
        if (a.is24GHz() != b.is24GHz()) return 0.0;
        double[] aPrimary = a.primaryRange();
        double[] aFull = a.fullRange();
        double[] bFull = b.fullRange();
        double primary = overlapLength(aPrimary, bFull) / (aPrimary[1] - aPrimary[0]);
        double full = overlapLength(aFull, bFull) / (aFull[1] - aFull[0]);
        return Math.max(primary, full);
    }

    /** 全電波の品質を計算し、電波の強い順に並べたリストを返す。 */
    public static List<Ap> evaluate(List<Ap> input) {
        List<Ap> list = new ArrayList<Ap>(input);
        for (Ap a : list) {
            a.channel = frequencyToChannel(a.frequency);
            a.band = frequencyToBand(a.frequency);
            a.coChannelCount = 0;
            a.overlapCount = 0;
            double load = 0.0;
            for (Ap b : list) {
                if (a == b || (a.bssid != null && a.bssid.equals(b.bssid))) continue;
                double o = overlapRatio(a, b);
                if (o <= 0.0) continue;
                double s = interfererStrength(b.rssi);
                if (s > 0.0) {
                    if (b.frequency == a.frequency) {
                        a.coChannelCount++;
                    } else {
                        a.overlapCount++;
                    }
                }
                load += o * s;
            }
            a.interferenceLoad = load;
            a.signalFactor = signalFactor(a.rssi, a.widthMhz);
            a.interferenceFactor = 1.0 / (1.0 + LOAD_WEIGHT * load);
            a.qualityPercent = (int) Math.round(a.signalFactor * a.interferenceFactor * 100.0);
        }
        Collections.sort(list, new Comparator<Ap>() {
            @Override
            public int compare(Ap x, Ap y) {
                if (x.rssi != y.rssi) return y.rssi - x.rssi;
                return y.qualityPercent - x.qualityPercent;
            }
        });
        return list;
    }

    public static String congestionLabel(double load) {
        if (load < 0.3) return "混線なし";
        if (load < 1.0) return "混線小";
        if (load < 2.5) return "混線中";
        return "混線大";
    }
}
