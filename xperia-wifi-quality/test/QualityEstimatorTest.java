import com.wifiquality.monitor.QualityEstimator;
import com.wifiquality.monitor.QualityEstimator.Ap;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** JVM 上で推定ロジックを検証する簡易テスト（依存ライブラリなし）。 */
public class QualityEstimatorTest {
    static int failures = 0;

    static void check(boolean cond, String msg) {
        System.out.println((cond ? "PASS " : "FAIL ") + msg);
        if (!cond) failures++;
    }

    static Ap ap(String bssid, int freq, int rssi) {
        return new Ap("net-" + bssid, bssid, freq, 0, 20, rssi);
    }

    public static void main(String[] args) {
        check(QualityEstimator.frequencyToChannel(2412) == 1, "2412MHz -> ch1");
        check(QualityEstimator.frequencyToChannel(2462) == 11, "2462MHz -> ch11");
        check(QualityEstimator.frequencyToChannel(2484) == 14, "2484MHz -> ch14");
        check(QualityEstimator.frequencyToChannel(5180) == 36, "5180MHz -> ch36");
        check(QualityEstimator.frequencyToChannel(5745) == 149, "5745MHz -> ch149");

        // 至近距離・混線なし = 100%
        List<Ap> single = QualityEstimator.evaluate(Arrays.asList(ap("a", 5180, -40)));
        check(single.get(0).qualityPercent == 100, "至近距離・単独 = 100% (" + single.get(0).qualityPercent + ")");

        // 信号係数は RSSI に対して単調減少
        double prev = 2;
        boolean mono = true;
        for (int r = -30; r >= -100; r--) {
            double s = QualityEstimator.signalFactor(r, 20);
            if (s > prev + 1e-9) mono = false;
            prev = s;
        }
        check(mono, "信号係数は単調減少");
        check(QualityEstimator.signalFactor(-95, 20) == 0.0, "-95dBm は 0%");

        // ビーコン占有時間（PHY 規格の式）
        double t1 = QualityEstimator.beaconAirtimeUs(200, 1, true);
        check(Math.abs(t1 - 1792) < 1e-9, "200B @1Mbps = 1792us (" + t1 + ")");
        double t6 = QualityEstimator.beaconAirtimeUs(200, 6, false);
        check(Math.abs(t6 - (20 + 4 * 68)) < 1e-9, "200B @6Mbps OFDM = 292us (" + t6 + ")");

        // 同一 ch に強い電波 1 つ（情報要素なし）→ 2.4GHz 典型使用率 20% → 混線係数 80%
        List<Ap> co = QualityEstimator.evaluate(Arrays.asList(ap("a", 2437, -40), ap("b", 2437, -50)));
        Ap a = find(co, "a");
        check(Math.abs(a.interferenceFactor - 0.80) < 1e-9, "2.4GHz 同ch 1つ -> 混線係数 80% (" + a.interferenceFactor + ")");
        check(a.coChannelCount == 1 && a.overlapCount == 0 && !a.measured, "同ch カウント・推定扱い");

        // 電波の数を増やしても典型値は増えない。ビーコン合計が典型値を超えたらビーコン合計
        List<Ap> many24 = new ArrayList<Ap>();
        many24.add(ap("a", 2437, -40));
        for (int i = 0; i < 15; i++) many24.add(ap("n" + i, 2437, -60));
        Ap dense = find(QualityEstimator.evaluate(many24), "a");
        double expectBeacons = 15 * 1792 / 102400.0;
        check(Math.abs(dense.primaryBusy - expectBeacons) < 1e-9,
                "同ch 15個 -> ビーコン合計 " + Math.round(expectBeacons * 100) + "% を採用");

        // 5GHz 同 ch → 典型使用率 2%
        Ap a5 = find(QualityEstimator.evaluate(Arrays.asList(ap("a", 5180, -40), ap("b", 5180, -50))), "a");
        check(Math.abs(a5.primaryBusy - 0.02) < 1e-9, "5GHz 同ch -> 使用率 2%");

        // BSS Load 実測値があればそれを使う（128/255 ≒ 50%）
        Ap m = ap("a", 2437, -40);
        m.bssLoadUtilization = 128;
        Ap mm = find(QualityEstimator.evaluate(Arrays.asList(m, ap("b", 2437, -50))), "a");
        check(mm.measured && Math.abs(mm.interferenceFactor - (1 - 128 / 255.0)) < 1e-9, "BSS Load 実測値を採用");

        // 対象 AP が BSS Load を出さなくても、同じ ch の別 AP の実測値を使う
        Ap rep = ap("b", 2437, -55);
        rep.bssLoadUtilization = 51;
        Ap viaRep = find(QualityEstimator.evaluate(Arrays.asList(ap("a", 2437, -40), rep)), "a");
        check(viaRep.measured && Math.abs(viaRep.primaryBusy - 51 / 255.0) < 1e-9, "同ch の他 AP の実測値を採用");

        // -82dBm 未満の同 ch 電波は送信を待たせない
        List<Ap> weak = QualityEstimator.evaluate(Arrays.asList(ap("a", 2437, -40), ap("b", 2437, -83)));
        check(find(weak, "a").qualityPercent == 100, "-83dBm の同ch は影響なし");
        List<Ap> cca = QualityEstimator.evaluate(Arrays.asList(ap("a", 2437, -40), ap("b", 2437, -82)));
        check(find(cca, "a").coChannelCount == 1, "-82dBm の同ch は検知");

        // 2.4GHz ch1 と ch6 は重ならない
        List<Ap> sep = QualityEstimator.evaluate(Arrays.asList(ap("a", 2412, -40), ap("b", 2437, -40)));
        check(find(sep, "a").qualityPercent == 100, "ch1 と ch6 は干渉なし");

        // ch1 と ch3（重なり 12/22 → -2.6dB）: -58dBm なら帯域内 -60.6dBm >= -62 → 検知、-62dBm なら -64.6 → 非検知
        Ap adjA = find(QualityEstimator.evaluate(Arrays.asList(ap("a", 2412, -40), ap("b", 2422, -58))), "a");
        check(adjA.overlapCount == 1 && adjA.qualityPercent == 80, "ch1/ch3 強い隣接 -> 検知 (" + adjA.qualityPercent + "%)");
        Ap adjB = find(QualityEstimator.evaluate(Arrays.asList(ap("a", 2412, -40), ap("b", 2422, -62))), "a");
        check(adjB.overlapCount == 0 && adjB.qualityPercent == 100, "ch1/ch3 弱い隣接 -> 非検知");

        // 2.4GHz と 5GHz は干渉しない
        List<Ap> bands = QualityEstimator.evaluate(Arrays.asList(ap("a", 2462, -40), ap("b", 5180, -40)));
        check(find(bands, "a").qualityPercent == 100, "異なるバンドは干渉なし");

        // 80MHz (ch36-48, primary 36) の AP: ch44 の 20MHz 電波はセカンダリ → 20MHz 縮退分だけ低下
        Ap wide = new Ap("w", "w", 5180, 5210, 80, -40);
        Ap w = find(QualityEstimator.evaluate(Arrays.asList(wide, ap("n", 5220, -50))), "w");
        double expectW = 1 - 0.02 * (1 - 20.0 / 80);
        check(w.secondaryCount == 1 && Math.abs(w.interferenceFactor - expectW) < 1e-9, "80MHz のセカンダリ干渉");
        // 逆に 20MHz ch44 の AP から見ると 80MHz AP はプライマリにかかる（復調可能）
        Ap n = find(QualityEstimator.evaluate(Arrays.asList(new Ap("w", "w", 5180, 5210, 80, -50), ap("n", 5220, -40))), "n");
        check(n.overlapCount == 1 && Math.abs(n.primaryBusy - 0.02) < 1e-9, "80MHz AP は ch44 のプライマリ干渉");

        // 情報要素の解析
        check(QualityEstimator.parseBssLoadUtilization(new byte[] {3, 0, (byte) 200, 0, 0}) == 200, "BSS Load 解析");
        double br = QualityEstimator.lowestBasicRate(new byte[] {(byte) 0x82, (byte) 0x84, 0x0c, 0x12}, -1);
        check(br == 1.0, "最低ベーシックレート 1Mbps");

        // 電波の強い順にソート
        List<Ap> many = new ArrayList<Ap>(Arrays.asList(
                ap("x", 2412, -80), ap("y", 5180, -45), ap("z", 2437, -60)));
        List<Ap> sorted = QualityEstimator.evaluate(many);
        check(sorted.get(0).bssid.equals("y") && sorted.get(1).bssid.equals("z") && sorted.get(2).bssid.equals("x"),
                "RSSI 降順ソート");

        System.out.println(failures == 0 ? "ALL PASSED" : failures + " FAILED");
        if (failures > 0) System.exit(1);
    }

    static Ap find(List<Ap> list, String bssid) {
        for (Ap a : list) if (a.bssid.equals(bssid)) return a;
        throw new AssertionError(bssid);
    }
}
