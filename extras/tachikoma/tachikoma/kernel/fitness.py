"""選択のルール (カーネル)。何を「改良」と認めるかは、タチコマ自身には決めさせない。

1. 圧縮・高速化の比較 (dominates / accept_variant)
2. 自己改変の試用期間 (canary): 変更前後の実運用の数値を比べる

指標はすべて、思考のコードが書き換えられない記録 (metrics / errors / predictions / beliefs /
train_samples の各表) からカーネルが直接計算する。利用者の判断基準との対応:

    合理性        brier (確信の較正。予測の当たり外れ)          低いほど良い
    知識の効率    knowledge_per_wh (確定した知識の数 / 消費電力量)  高いほど良い
    性能          step_p90 / latency_p90 (思考ループ・返事の所要時間)  低いほど良い
    電力          watts_mean (平均消費電力)                       低いほど良い
    頑健さ        errors_per_kstep                                 低いほど良い
    関係          good_ratio (/good と /bad・訂正の比)             高いほど良い
    先見          foresight_auc (拾ったときの優先度が、後で役立った情報ほど高かったか)  高いほど良い
"""

from .foresight import window_auc
from .metrics import summary

LOWER_IS_BETTER = {"brier", "step_p90", "latency_p90", "watts_mean", "errors_per_kstep"}


# ---------------------------------------------------------------- 1. 圧縮・高速化
def dominates(a, b):
    ge = (a["quality"] >= b["quality"] and a["speed"] >= b["speed"] and a["memory"] <= b["memory"])
    gt = (a["quality"] > b["quality"] or a["speed"] > b["speed"] or a["memory"] < b["memory"])
    return ge and gt


def pareto_front(points):
    return [p for p in points if not any(dominates(q, p) for q in points if q is not p)]


def accept_variant(ref, cand, cfg):
    drop = cfg["lab_max_quality_drop"]
    if cand["quality"] < ref["quality"] * (1 - drop):
        return False, f"品質低下が大きい ({cand['quality']:.3f} < {ref['quality']:.3f}×{1 - drop:.2f})"
    if cand.get("fidelity", 1.0) < cfg["lab_min_fidelity"]:
        return False, f"本番との一致率が低い ({cand.get('fidelity', 0):.2f})"
    gain = cfg["lab_min_gain"]
    if not (cand["speed"] >= ref["speed"] * (1 + gain) or cand["memory"] <= ref["memory"] * (1 - gain)):
        return False, "速度にもメモリにも十分な改善がない"
    return True, "改善"


# ---------------------------------------------------------------- 2. 実運用の指標
def _has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _mean(v):
    return sum(v) / len(v) if v else None


def window_stats(metrics, db, start, end):
    hours = max(1e-6, (end - start) / 3600)
    good = bad = 0
    if _has(db, "train_samples"):
        good = db.execute("SELECT COUNT(*) FROM train_samples WHERE origin='user_feedback' AND kind='chat'"
                          " AND ts>=? AND ts<?", (start, end)).fetchone()[0]
        bad = db.execute("SELECT COUNT(*) FROM train_samples WHERE (origin='user_correction' OR kind='rejected')"
                         " AND ts>=? AND ts<?", (start, end)).fetchone()[0]
    brier = None
    if _has(db, "predictions"):
        rows = db.execute("SELECT p, outcome FROM predictions WHERE outcome IS NOT NULL AND resolved_ts>=?"
                          " AND resolved_ts<?", (start, end)).fetchall()
        brier = _mean([(r[0] - r[1]) ** 2 for r in rows]) if len(rows) >= 5 else None
    knowledge = 0
    if _has(db, "beliefs"):
        # 外部の根拠で確定した知識 (ラベルの規則をここで独立に持つ: 思考のコードの書き換えに左右されない)
        knowledge = db.execute(
            "SELECT COUNT(*) FROM beliefs WHERE updated>=? AND updated<? AND ("
            " (p>=0.9 AND source IN ('observation','user')) OR p<=0.1 OR (p>=0.7 AND source='research'))",
            (start, end)).fetchone()[0]
    steps = metrics.values("step_s", start, end)
    step = summary(steps)
    lat = summary(metrics.values("reply_latency_s", start, end))
    watts = metrics.values("watts", start, end)
    wh = (_mean(watts) or 0) * hours
    return {
        "hours": hours,
        "steps": len(steps),
        "errors_per_kstep": 1000 * metrics.count("error", start, end) / max(1, len(steps)),
        "step_p90": step["p90"] if step else None,
        "latency_p90": lat["p90"] if lat else None,
        "feedback_n": good + bad,
        "good_ratio": good / (good + bad) if good + bad >= 3 else None,
        "brier": brier,
        "knowledge": knowledge,
        "knowledge_per_wh": knowledge / wh if wh > 0 else None,
        "watts_mean": _mean(watts),
        "foresight_auc": window_auc(db, start, end, metrics_horizon(end - start)),
    }


def metrics_horizon(span_s):
    """先見を測る期限: 窓の半分 (最大 6 時間)。窓の前半に拾った情報が、期限内に役立ったかを見る。"""
    return min(6 * 3600, span_s / 2)


def compare(before, after, cfg):
    """害が無いかの判定: 'keep' / 'revert' / 'wait'。"""
    if after["steps"] < cfg["canary_min_steps"]:
        return "wait", "試用期間のデータがまだ足りない"
    if after["errors_per_kstep"] > before["errors_per_kstep"] * 1.2 + 1:
        return "revert", f"エラーが増えた ({before['errors_per_kstep']:.1f}→{after['errors_per_kstep']:.1f}/千step)"
    for key, tol, label in (("latency_p90", 0.25, "返事が遅くなった"), ("step_p90", 0.25, "思考ループが遅くなった"),
                            ("watts_mean", 0.10, "消費電力が増えた")):
        if before.get(key) and after.get(key) and after[key] > before[key] * (1 + tol):
            return "revert", f"{label} ({before[key]:.2f}→{after[key]:.2f})"
    if (after["feedback_n"] >= 5 and before["good_ratio"] is not None and after["good_ratio"] is not None
            and after["good_ratio"] < before["good_ratio"] - 0.15):
        return "revert", f"評価が下がった ({before['good_ratio']:.2f}→{after['good_ratio']:.2f})"
    if before["brier"] is not None and after["brier"] is not None and after["brier"] > before["brier"] + 0.05:
        return "revert", f"確信の較正が悪化した (Brier {before['brier']:.3f}→{after['brier']:.3f})"
    if (before.get("knowledge_per_wh") and after.get("knowledge_per_wh") is not None
            and after["knowledge_per_wh"] < before["knowledge_per_wh"] * 0.75):
        return "revert", "1Wh あたりに増える知識が減った"
    return "keep", "悪化は見られない"


def improved(metric, before, after, min_gain):
    """狙った指標が改善したか: True / False / None (測れない)。"""
    b, a = before.get(metric), after.get(metric)
    if b is None or a is None:
        return None
    if metric in LOWER_IS_BETTER:
        return a <= b * (1 - min_gain) if b > 0 else a < b
    return a >= b * (1 + min_gain) if b > 0 else a > b
