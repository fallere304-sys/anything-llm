package com.wifiquality.monitor;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/**
 * 推定通信品質の計算（Android API に依存しない純粋な Java）。
 *
 * 推定品質[%] = 信号係数 × 混線係数 × 100（至近距離・混線なし = 100%）
 *
 * ■ 信号係数
 *   IEEE 802.11n/ac の受信最小感度（20MHz）で到達できる MCS の伝送レート / MCS9(86.7Mbps)。
 *   チャネル幅が 2 倍になるごとに必要感度は 3dB 上がる（規格の感度表と同じ関係）。
 *
 * ■ 混線係数（CSMA/CA: 媒体がビジーの間は送信できない）
 *   混線係数 = (1 - Up) × (1 - Us × (1 - 20/W))
 *     Up: プライマリ 20MHz チャネルが他者で埋まっている時間率 → その間は送信不可
 *     Us: セカンダリ（40/80/160MHz 時の残り帯域）のビジー率 → その間は 20MHz に縮退
 *     W : 自分のチャネル幅 [MHz]
 *
 *   Up / Us の求め方（優先順）
 *   1. 実測: AP がビーコンで報告する BSS Load 要素（Element ID 11）の Channel Utilization
 *      （IEEE 802.11 の定義で「AP が媒体をビジーと検知した時間率 ×255」）。
 *      対象 AP 自身の値、無ければ同じプライマリチャネルで最も強い AP の値を使う。
 *   2. 推定: 「送信を待たされる」他の電波（下記）が 1 つ以上あるとき
 *      max(それらのビーコン占有率の合計, バンドの典型使用率)。
 *      - ビーコン占有率: PHY 規格のフレーム時間 / ビーコン間隔 100TU(102.4ms) で計算
 *      - 典型使用率: 約1万台の AP の実測 (Biswas et al., SIGCOMM 2015)
 *        2.4GHz: 大半の AP で 20% 以上 → 20%、5GHz: 平均 2% → 2%
 *        （同論文は「チャネル上のネットワーク数は使用率を予測しない」と報告している。
 *          そのため電波の数に比例させず、1 つ以上あれば典型値を適用する）
 *
 *   「送信を待たされる」判定（IEEE 802.11 OFDM PHY の CCA 要件）
 *     - 自分のプライマリ ch で相手のプリアンブルを復調できる場合: 受信電力 -82dBm 以上
 *     - 復調できない部分的な重なり（2.4GHz の隣接 ch など）: 帯域内エネルギー -62dBm 以上
 *       帯域内エネルギー = RSSI + 10log10(重なり幅 / 相手の占有幅)
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

        // 情報要素（Android 7 以降で取得できた場合のみ。未取得は負値）
        public int bssLoadUtilization = -1; // BSS Load の Channel Utilization (0〜255)
        public int beaconBytes = -1;        // ビーコンのフレーム長 [byte]
        public double basicRateMbps = -1;   // 最低ベーシックレート [Mbps]

        // 計算結果
        public int channel;
        public String band;
        public int coChannelCount;    // 同じプライマリ ch で送信を待たされる電波数
        public int overlapCount;      // 部分的な重なりで送信を待たされる電波数
        public int secondaryCount;    // セカンダリ帯域のみにかかる電波数
        public double primaryBusy;    // Up
        public double secondaryBusy;  // Us
        public boolean measured;      // Up が BSS Load の実測値か
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

        /** プライマリ 20MHz の占有帯域 [low, high]。2.4GHz は DSSS 互換の 22MHz 幅で扱う。 */
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

        /** freq がこのチャネルの 20MHz サブチャネル中心に一致するか（= プリアンブルを復調できる）。 */
        boolean hasSubchannelAt(int freq) {
            if (freq == frequency) return true;
            if (widthMhz <= 20) return false;
            double[] r = fullRange();
            if (freq <= r[0] || freq >= r[1]) return false;
            double offset = freq - (r[0] + 10);
            return Math.abs(offset / 20.0 - Math.rint(offset / 20.0)) < 1e-6;
        }
    }

    // RSSI [dBm] → 信号係数（IEEE 802.11n/ac 受信最小感度 20MHz と MCS レート / 86.7Mbps）
    private static final double[][] SIGNAL_TABLE = {
        {-57, 1.000}, // MCS9 256QAM 5/6  86.7Mbps
        {-59, 0.900}, // MCS8 256QAM 3/4  78.0
        {-64, 0.750}, // MCS7 64QAM 5/6   65.0
        {-65, 0.675}, // MCS6 64QAM 3/4   58.5
        {-66, 0.600}, // MCS5 64QAM 2/3   52.0
        {-70, 0.450}, // MCS4 16QAM 3/4   39.0
        {-74, 0.300}, // MCS3 16QAM 1/2   26.0
        {-77, 0.225}, // MCS2 QPSK 3/4    19.5
        {-79, 0.150}, // MCS1 QPSK 1/2    13.0
        {-82, 0.075}, // MCS0 BPSK 1/2     6.5
        {-90, 0.000},
    };

    /** CCA: 復調可能なプリアンブルを検知する受信電力 [dBm]（OFDM PHY, 20MHz）。 */
    public static final double CCA_PREAMBLE_DBM = -82;
    /** CCA: 復調できない信号をエネルギーで検知する閾値 [dBm]（最小感度 + 20dB）。 */
    public static final double CCA_ENERGY_DBM = -62;

    /** ビーコン間隔 100TU = 102.4ms（dot11BeaconPeriod の既定値）[µs]。 */
    public static final double BEACON_INTERVAL_US = 102400;

    /**
     * 情報要素を取得できないときのビーコン長 [byte]。WPA2 / 802.11n AP の最小構成から計算:
     * MACヘッダ24 + 固定部12 + SSID(2+8) + Rates(2+8) + DS(3) + TIM(2+4) + Country(2+6)
     * + ERP(3) + ExtRates(2+4) + RSN(2+20) + HTcap(2+26) + HTop(2+22) + ExtCap(2+8)
     * + WMM(2+24) + FCS4 = 196 → 200。ベンダ独自要素を含めない下限寄りの値。
     */
    public static final int DEFAULT_BEACON_BYTES = 200;

    /** バンドごとの典型チャネル使用率（Biswas et al., SIGCOMM 2015）。 */
    public static final double TYPICAL_UTIL_24GHZ = 0.20;
    public static final double TYPICAL_UTIL_5GHZ = 0.02;

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

    /**
     * ビーコン 1 回の送信時間 [µs]。
     * DSSS/CCK (1, 2, 5.5, 11Mbps): 長プリアンブル 192µs + 8L / rate
     * OFDM (6〜54Mbps): 20µs + 4µs × ceil((16 + 8L + 6) / (4 × rate))、2.4GHz は信号拡張 6µs を加算
     */
    public static double beaconAirtimeUs(int bytes, double rateMbps, boolean is24GHz) {
        if (rateMbps == 1 || rateMbps == 2 || rateMbps == 5.5 || rateMbps == 11) {
            return 192 + 8.0 * bytes / rateMbps;
        }
        double bitsPerSymbol = 4 * rateMbps;
        double symbols = Math.ceil((16 + 8.0 * bytes + 6) / bitsPerSymbol);
        return 20 + 4 * symbols + (is24GHz ? 6 : 0);
    }

    /** その AP のビーコンがチャネルを占有する時間率。 */
    public static double beaconUtilization(Ap b) {
        int bytes = b.beaconBytes > 0 ? b.beaconBytes : DEFAULT_BEACON_BYTES;
        // 情報要素が無いとき: 2.4GHz は 802.11b 互換の 1Mbps、5GHz は必須最低レートの 6Mbps
        double rate = b.basicRateMbps > 0 ? b.basicRateMbps : (b.is24GHz() ? 1 : 6);
        return beaconAirtimeUs(bytes, rate, b.is24GHz()) / BEACON_INTERVAL_US;
    }

    private static double overlapLength(double[] a, double[] b) {
        return Math.max(0.0, Math.min(a[1], b[1]) - Math.max(a[0], b[0]));
    }

    private static double typicalUtilization(Ap a) {
        return a.is24GHz() ? TYPICAL_UTIL_24GHZ : TYPICAL_UTIL_5GHZ;
    }

    /** 全電波の品質を計算し、電波の強い順に並べたリストを返す。 */
    public static List<Ap> evaluate(List<Ap> input) {
        List<Ap> list = new ArrayList<Ap>(input);
        for (Ap a : list) {
            evaluateOne(a, list);
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

    private static void evaluateOne(Ap a, List<Ap> all) {
        a.channel = frequencyToChannel(a.frequency);
        a.band = frequencyToBand(a.frequency);
        a.coChannelCount = 0;
        a.overlapCount = 0;
        a.secondaryCount = 0;

        double[] aPrimary = a.primaryRange();
        double[] aFull = a.fullRange();
        double primaryBeacons = 0.0;
        double secondaryBeacons = 0.0;
        Ap reporter = a.bssLoadUtilization >= 0 ? a : null;

        for (Ap b : all) {
            if (b == a || (a.bssid != null && a.bssid.equals(b.bssid))) continue;
            if (a.is24GHz() != b.is24GHz()) continue;
            double[] bFull = b.fullRange();
            double bWidth = bFull[1] - bFull[0];

            if (b.hasSubchannelAt(a.frequency)) {
                // プライマリ上で復調可能 → -82dBm 以上で送信を待たされる
                if (b.rssi >= CCA_PREAMBLE_DBM) {
                    if (b.frequency == a.frequency) a.coChannelCount++; else a.overlapCount++;
                    primaryBeacons += beaconUtilization(b);
                }
                if (b.frequency == a.frequency && b.bssLoadUtilization >= 0
                        && (reporter == null || (reporter != a && b.rssi > reporter.rssi))) {
                    reporter = b;
                }
                continue;
            }
            double onPrimary = overlapLength(aPrimary, bFull);
            if (onPrimary > 0) {
                // 部分的な重なり → 帯域内エネルギーが -62dBm 以上で送信を待たされる
                double inBand = b.rssi + 10 * Math.log10(onPrimary / bWidth);
                if (inBand >= CCA_ENERGY_DBM) {
                    a.overlapCount++;
                    primaryBeacons += beaconUtilization(b);
                }
                continue;
            }
            double onSecondary = overlapLength(aFull, bFull);
            if (onSecondary > 0 && a.widthMhz > 20) {
                double inBand = b.rssi + 10 * Math.log10(onSecondary / bWidth);
                if (inBand >= CCA_ENERGY_DBM) {
                    a.secondaryCount++;
                    secondaryBeacons += beaconUtilization(b);
                }
            }
        }

        boolean primaryContended = a.coChannelCount + a.overlapCount > 0;
        if (reporter != null) {
            a.measured = true;
            a.primaryBusy = reporter.bssLoadUtilization / 255.0;
        } else {
            a.measured = false;
            a.primaryBusy = primaryContended ? Math.max(primaryBeacons, typicalUtilization(a)) : 0.0;
        }
        a.secondaryBusy = a.secondaryCount > 0 ? Math.max(secondaryBeacons, typicalUtilization(a)) : 0.0;
        a.primaryBusy = clamp01(a.primaryBusy);
        a.secondaryBusy = clamp01(a.secondaryBusy);

        double widthLoss = a.widthMhz > 20 ? 1.0 - 20.0 / a.widthMhz : 0.0;
        a.signalFactor = signalFactor(a.rssi, a.widthMhz);
        a.interferenceFactor = (1.0 - a.primaryBusy) * (1.0 - a.secondaryBusy * widthLoss);
        a.qualityPercent = (int) Math.round(a.signalFactor * a.interferenceFactor * 100.0);
    }

    private static double clamp01(double v) {
        return Math.max(0.0, Math.min(1.0, v));
    }

    /** 混線係数に応じたラベル。 */
    public static String congestionLabel(double interferenceFactor) {
        if (interferenceFactor >= 0.95) return "混線なし";
        if (interferenceFactor >= 0.80) return "混線小";
        if (interferenceFactor >= 0.50) return "混線中";
        return "混線大";
    }

    // ---- 情報要素の解析 ----

    /** BSS Load 要素 (ID 11) の本体から Channel Utilization (0〜255) を取り出す。 */
    public static int parseBssLoadUtilization(byte[] body) {
        // Station Count(2) + Channel Utilization(1) + Available Admission Capacity(2)
        if (body == null || body.length < 3) return -1;
        return body[2] & 0xFF;
    }

    /** Supported Rates (ID 1) / Extended Supported Rates (ID 50) の本体から最低ベーシックレート [Mbps] を求める。 */
    public static double lowestBasicRate(byte[] body, double current) {
        if (body == null) return current;
        double best = current;
        for (byte v : body) {
            if ((v & 0x80) != 0) {
                double mbps = (v & 0x7F) * 0.5;
                if (mbps > 0 && (best <= 0 || mbps < best)) best = mbps;
            }
        }
        return best;
    }

    /** 情報要素の長さの合計からビーコン長を求める（MACヘッダ 24 + 固定部 12 + FCS 4）。 */
    public static int beaconLengthFromIeBytes(int ieTotalBytes) {
        return 24 + 12 + ieTotalBytes + 4;
    }
}
