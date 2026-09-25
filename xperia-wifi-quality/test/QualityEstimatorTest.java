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

        // 同一チャネル強電波 1 つ → 混線係数 2/3
        List<Ap> co = QualityEstimator.evaluate(Arrays.asList(ap("a", 2437, -40), ap("b", 2437, -50)));
        Ap a = find(co, "a");
        check(Math.abs(a.interferenceFactor - 2.0 / 3) < 1e-9, "同ch 強電波1つ -> 混線係数 67%");
        check(a.coChannelCount == 1 && a.overlapCount == 0, "同ch カウント");

        // 2.4GHz ch1 と ch6 は重ならない / ch1 と ch3 は部分的に重なる
        List<Ap> sep = QualityEstimator.evaluate(Arrays.asList(ap("a", 2412, -40), ap("b", 2437, -40)));
        check(find(sep, "a").qualityPercent == 100, "ch1 と ch6 は干渉なし");
        List<Ap> adj = QualityEstimator.evaluate(Arrays.asList(ap("a", 2412, -40), ap("b", 2422, -40)));
        Ap adjA = find(adj, "a");
        check(adjA.overlapCount == 1 && adjA.qualityPercent < 100 && adjA.qualityPercent > 67,
                "ch1 と ch3 は部分干渉 (" + adjA.qualityPercent + "%)");

        // 2.4GHz と 5GHz は干渉しない
        List<Ap> bands = QualityEstimator.evaluate(Arrays.asList(ap("a", 2462, -40), ap("b", 5180, -40)));
        check(find(bands, "a").qualityPercent == 100, "異なるバンドは干渉なし");

        // 80MHz 幅の AP に対し、その帯域内の 20MHz 電波は干渉する
        Ap wide = new Ap("w", "w", 5180, 5210, 80, -40);
        List<Ap> w = QualityEstimator.evaluate(Arrays.asList(wide, ap("n", 5240, -50)));
        check(find(w, "w").overlapCount == 1 && find(w, "w").qualityPercent < 100, "80MHz 帯域内の電波を検出");

        // 弱い電波 (-92dBm) は混線に数えない
        List<Ap> weak = QualityEstimator.evaluate(Arrays.asList(ap("a", 2437, -40), ap("b", 2437, -92)));
        check(find(weak, "a").qualityPercent == 100, "-92dBm は混線に影響しない");

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
