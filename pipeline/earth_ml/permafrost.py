"""Permafrost / ground-ice classifier for Earth cells, trained on ESA CCI satellite labels.

Why: CCI Permafrost only covers 25-85 N. Several Mars analogs (Antarctic Dry Valleys,
high Andes / Atacama) are outside it, so the app needs a model that says "ground ice:
yes / no / unsure" there, and that model has to be honestly evaluated.

Labels (ESA CCI Permafrost 2021, permafrost fraction PFR, mean over each 0.5 deg cell):
    ice = 1 if PFR >= 0.5,  ice = 0 if PFR < 0.1,  cells in between are ambiguous and dropped.
Predictors (Earth Engine export, ee_export.py): ERA5-Land soil temperature levels 1-4 and skin
temperature (means + annual ranges), MODIS day/night land-surface temperature, elevation, slope,
roughness, aridity, SoilGrids sand/clay/bulk density, GLiM rock class, latitude.
NEVER used as predictors: perm_prob, ice_label, ground_label, the CCI layers themselves, or
anything else derived from the labels (LEAKY below; tests/test_permafrost.py enforces it).

Evaluation (output/ml_report.json):
  1. spatial block CV (10 deg blocks, GroupKFold),
  2. leave-region-out: train without Alaska / Tibet / Svalbard / Siberia, test on it,
  3. baselines on the same splits: majority class, latitude-only logistic regression,
     and the old temperature rule (ice if skin T < COLD_K, no ice if > WARM_K, build_layers.py),
  4. metrics: F1 (ice class), balanced accuracy, ROC-AUC,
  5. permutation importance, isotonic calibration (Brier before/after) and split-conformal
     90% prediction sets: cells whose set is {0, 1} are "uncertain".
Hyperparameters: config processing.gap_model (not tuned on these results).

Physics guard: CCI labels only reach ground up to ~288 K, so the trees cannot know what happens in
hot deserts and drifted towards "ice" there (hyper-arid = cold desert in the training data). Permafrost
is ground at or below 0 C, so where the deepest ERA5-Land layer (1-2.9 m) never gets below 0 C in any
month (mean - range/2 > 273.15 K) the guard sets P(ice) = 0. It uses predictor columns only, and the
report keeps the unconstrained model ("model_unguarded") next to it so the effect is visible.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, f1_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from ..common.config import get_processing
from . import permafrost_nn

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "output" / "ml_report.json"

NUMERIC = [
    "soil_temperature_level_1_mean", "soil_temperature_level_2_mean", "soil_temperature_level_3_mean",
    "soil_temperature_level_4_mean", "skin_temperature_mean",
    "soil_temperature_level_1_range", "soil_temperature_level_4_range", "skin_temperature_range",
    "lst_day_k", "lst_night_k", "elevation_m", "slope_deg", "roughness_m", "aridity",
    "sand_0_2m", "clay_0_2m", "bdod_0_2m", "lat",
]
CATEGORICAL = ["glim_class"]
PREDICTORS = NUMERIC + CATEGORICAL
# Label-derived or label-adjacent columns that must never be inputs.
LEAKY = {"perm_prob", "ice_label", "ground_label", "pfr", "alt_m", "cci_pfr", "cci_alt", "ice", "ice_q",
         "ground_class", "ground_class_q", "antarctic_assumed", "ice_prob", "ice_set"}

ICE_MIN, NO_ICE_MAX = 0.5, 0.1          # PFR thresholds for the labels
ALPHA = 0.10                             # conformal: 90% prediction sets
BLOCK_DEG = 10
# Leave-region-out boxes: (lat_min, lat_max, lon_min, lon_max)
REGIONS = {
    "Alaska": (51, 72, -170, -129),
    "Tibet": (26, 40, 73, 105),
    "Svalbard": (76, 81, 9, 35),
    "Siberia": (50, 78, 90, 180),
}
WARM_K, COLD_K = 278.0, 271.15          # same numbers as build_layers (the rule baseline)
GLIM_CODES = ["su", "ss", "sm", "sc", "ev", "va", "vi", "vb", "py", "ig", "pa", "pi", "pb", "mt", "wb", "nd"]
SANITY = {   # outside CCI coverage; checked in tests/test_permafrost.py
    "Dry Valleys (University Valley)": (-77.86, 160.74, 1),
    "Death Valley": (36.46, -116.87, 0),
    "Atacama lowlands (Yungay)": (-24.08, -70.02, 0),
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def make_labels(pfr):
    """1 / 0 / NaN (ambiguous or outside CCI) from the CCI permafrost fraction."""
    pfr = np.asarray(pfr, float)
    y = np.full(len(pfr), np.nan)
    y[pfr >= ICE_MIN] = 1.0
    y[pfr < NO_ICE_MAX] = 0.0
    return y


def features(df):
    """Predictor matrix. Only PREDICTORS; missing numeric columns become NaN (HGB handles NaN)."""
    X = pd.DataFrame(index=df.index)
    for c in NUMERIC:
        X[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    g = df["glim_class"] if "glim_class" in df else pd.Series("nd", index=df.index)
    X["glim_class"] = pd.Categorical(g.fillna("nd").astype(str), categories=GLIM_CODES).codes.astype(float)
    X.loc[X["glim_class"] < 0, "glim_class"] = np.nan
    assert not (set(X.columns) & LEAKY), "leaky predictor"
    return X


def blocks(df, deg=BLOCK_DEG):
    return (np.floor(df["lat"] / deg).astype(int) * 1000 + np.floor(df["lon"] / deg).astype(int)).to_numpy()


def in_region(df, name):
    a, b, c, d = REGIONS[name]
    return ((df["lat"] >= a) & (df["lat"] <= b) & (df["lon"] >= c) & (df["lon"] <= d)).to_numpy()


# ---------------------------------------------------------------------------
# Models and baselines
# ---------------------------------------------------------------------------

def model(gp):
    cat = [PREDICTORS.index(c) for c in CATEGORICAL]
    return HistGradientBoostingClassifier(max_iter=gp["n_estimators"], max_depth=gp["max_depth"],
                                          learning_rate=gp["learning_rate"], categorical_features=cat,
                                          random_state=0)


def never_frozen(X):
    """True where the 1-2.9 m soil layer's coldest month is above 0 C (no permafrost possible)."""
    mean = X["soil_temperature_level_4_mean"].to_numpy(float)
    rng = X["soil_temperature_level_4_range"].to_numpy(float)
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(mean - rng / 2, nan=-np.inf) > 273.15


def guard(p, X):
    p = np.array(p, float)
    p[never_frozen(X)] = 0.0
    return p


def rule_scores(X):
    """Old temperature rule: probability-like score from skin temperature (colder = more ice)."""
    t = X["skin_temperature_mean"].to_numpy(float)
    pred = np.where(t < COLD_K, 1, 0)                       # WARM_K..COLD_K band counts as no ice
    score = np.clip((WARM_K - t) / (WARM_K - COLD_K), 0, 1)
    return pred, np.where(np.isnan(t), 0.5, score)


def fit_predict_all(gp, X_tr, y_tr, X_te):
    """Model + baselines trained on (X_tr, y_tr), scored on X_te: {name: (pred, score)}."""
    out = {}
    m = model(gp).fit(X_tr, y_tr)
    raw = m.predict_proba(X_te)[:, 1]
    p = guard(raw, X_te)
    out["model"] = ((p >= 0.5).astype(int), p)
    out["model_unguarded"] = ((raw >= 0.5).astype(int), raw)
    maj = int(np.round(y_tr.mean()))
    out["majority"] = (np.full(len(X_te), maj), np.full(len(X_te), float(y_tr.mean())))
    lat_tr = X_tr[["lat"]].to_numpy()
    lr = LogisticRegression().fit(np.hstack([lat_tr, np.abs(lat_tr)]), y_tr)
    lat_te = X_te[["lat"]].to_numpy()
    q = lr.predict_proba(np.hstack([lat_te, np.abs(lat_te)]))[:, 1]
    out["latitude_logistic"] = ((q >= 0.5).astype(int), q)
    out["temperature_rule"] = rule_scores(X_te)
    if permafrost_nn.available():
        # Neural-net comparison (optional, PyTorch): same features, same folds, same physics guard.
        raw = permafrost_nn.fit_predict(X_tr, y_tr, X_te)
        p = guard(raw, X_te)
        out["nn"] = ((p >= 0.5).astype(int), p)
    return out


def metrics(y, pred, score):
    y = np.asarray(y, int)
    res = {"n": int(len(y)), "n_ice": int(y.sum()),
           "f1": round(float(f1_score(y, pred, zero_division=0)), 3)}
    both = len(np.unique(y)) == 2
    res["balanced_accuracy"] = round(float(balanced_accuracy_score(y, pred)), 3) if both else None
    res["roc_auc"] = round(float(roc_auc_score(y, score)), 3) if both else None
    return res


def spatial_cv(gp, X, y, groups, n_splits=5):
    names = ["model", "model_unguarded", "majority", "latitude_logistic", "temperature_rule"]
    if permafrost_nn.available():
        names.append("nn")
    preds = {n: np.zeros(len(y), int) for n in names}
    scores = {n: np.zeros(len(y)) for n in names}
    for tr, te in GroupKFold(n_splits=n_splits).split(X, y, groups):
        for n, (pr, sc) in fit_predict_all(gp, X.iloc[tr], y[tr], X.iloc[te]).items():
            preds[n][te], scores[n][te] = pr, sc
    return {n: metrics(y, preds[n], scores[n]) for n in names}


def leave_region_out(gp, df, X, y):
    out = {}
    for name in REGIONS:
        test = in_region(df, name)
        if test.sum() < 5:
            out[name] = {"note": f"only {int(test.sum())} labelled cells; skipped"}
            continue
        res = fit_predict_all(gp, X[~test], y[~test], X[test])
        out[name] = {n: metrics(y[test], pr, sc) for n, (pr, sc) in res.items()}
    return out


# ---------------------------------------------------------------------------
# Calibration + split-conformal prediction sets
# ---------------------------------------------------------------------------

def conformal_threshold(p_ice, y, alpha=ALPHA):
    """Split-conformal: nonconformity = 1 - p(true class); q = the ceil((n+1)(1-alpha))/n quantile."""
    p_true = np.where(np.asarray(y) == 1, p_ice, 1 - p_ice)
    s = np.sort(1 - p_true)
    n = len(s)
    k = min(n - 1, int(np.ceil((n + 1) * (1 - alpha))) - 1)
    return float(s[k])


def prediction_sets(p_ice, q):
    """Boolean (has_no_ice, has_ice) per cell: class c is in the set when 1 - p(c) <= q."""
    p_ice = np.asarray(p_ice, float)
    return p_ice <= q, (1 - p_ice) <= q


def fit_final(gp, X, y, groups, seed=0):
    """Calibrated model on ~80% of the blocks, conformal threshold from the other ~20%."""
    rng = np.random.default_rng(seed)
    ub = np.unique(groups)
    cal_blocks = rng.choice(ub, size=max(1, len(ub) // 5), replace=False)
    cal = np.isin(groups, cal_blocks)
    raw = model(gp).fit(X[~cal], y[~cal])
    splits = list(GroupKFold(n_splits=min(5, len(np.unique(groups[~cal])))).split(X[~cal], y[~cal], groups[~cal]))
    calibrated = CalibratedClassifierCV(model(gp), method="isotonic", cv=splits).fit(X[~cal], y[~cal])
    p_raw = guard(raw.predict_proba(X[cal])[:, 1], X[cal])
    p_cal = guard(calibrated.predict_proba(X[cal])[:, 1], X[cal])
    # Conformal threshold on half of the held-out blocks, coverage checked on the other half.
    cal_ub = np.unique(groups[cal])
    half = np.isin(groups[cal], cal_ub[::2])
    q = conformal_threshold(p_cal[half], y[cal][half])
    no_ice, ice = prediction_sets(p_cal[~half], q)
    yt = y[cal][~half]
    covered = np.where(yt == 1, ice, no_ice)
    info = {
        "calibration_cells": int(cal.sum()),
        "brier_raw": round(float(brier_score_loss(y[cal], p_raw)), 4),
        "brier_calibrated": round(float(brier_score_loss(y[cal], p_cal)), 4),
        "conformal": {"alpha": ALPHA, "threshold": round(q, 4),
                      "empirical_coverage": round(float(covered.mean()), 3),
                      "share_uncertain": round(float((no_ice & ice).mean()), 3),
                      "n_test": int(len(yt))},
    }
    # Final conformal threshold from all calibration cells.
    info["conformal"]["threshold_final"] = round(conformal_threshold(p_cal, y[cal]), 4)
    return {"model": calibrated, "raw": raw, "q": info["conformal"]["threshold_final"]}, info, (X[cal], y[cal])


def predict(bundle, df):
    """(p_ice, label 0/1/NaN) for every row. NaN = conformal set {0, 1}: the model won't commit
    (uncertain). An empty set (possible when q < 0.5) falls back to the more likely class."""
    X = features(df)
    p = guard(bundle["model"].predict_proba(X)[:, 1], X)
    no_ice, ice = prediction_sets(p, bundle["q"])
    label = np.where(ice & ~no_ice, 1.0, np.where(no_ice & ~ice, 0.0, np.nan))
    empty = ~ice & ~no_ice
    label[empty] = (p[empty] >= 0.5).astype(float)
    return p, label


# ---------------------------------------------------------------------------
# Train + report
# ---------------------------------------------------------------------------

def _verdict(lro, spatial):
    """Plain-language comparison against every baseline, per metric (no cherry-picking)."""
    lines, wins = [], {}
    baselines = ["latitude_logistic", "temperature_rule", "majority"]
    regions = [r for r, v in lro.items() if "model" in v]
    for metric in ("f1", "balanced_accuracy", "roc_auc"):
        wins[metric] = {}
        for b in baselines:
            usable = [r for r in regions if lro[r]["model"][metric] is not None and lro[r][b][metric] is not None]
            w = sum(lro[r]["model"][metric] > lro[r][b][metric] for r in usable)
            wins[metric][b] = {"wins": int(w), "regions": len(usable)}
    for b in baselines:
        f, ba = wins["f1"][b], wins["balanced_accuracy"][b]
        lines.append(f"Leave-region-out vs {b.replace('_', ' ')}: model has higher F1 in {f['wins']} of {f['regions']} "
                     f"regions and higher balanced accuracy in {ba['wins']} of {ba['regions']}.")
    if "nn" in spatial:
        for metric in ("f1", "roc_auc"):
            usable = [r for r in regions if lro[r]["model"][metric] is not None and lro[r]["nn"][metric] is not None]
            w = sum(lro[r]["nn"][metric] > lro[r]["model"][metric] for r in usable)
            lines.append(f"Neural net (PyTorch MLP) vs gradient boosting, leave-region-out {metric.replace('_', ' ')}: "
                         f"the neural net is higher in {w} of {len(usable)} regions; spatial CV "
                         f"{spatial['nn'][metric]} vs {spatial['model'][metric]}.")
    for metric in ("f1", "balanced_accuracy"):
        m = spatial["model"][metric]
        best_name = max(baselines, key=lambda b: spatial[b][metric] or 0)
        best = spatial[best_name][metric]
        lines.append(f"Spatial block CV {metric.replace('_', ' ')}: model {m} vs best baseline {best} "
                     f"({best_name.replace('_', ' ')})" + (" - model is better." if m > best else " - model does NOT beat it."))
    return lines, wins


def train_and_report(df, params=None, report_path=REPORT_PATH):
    """df: Earth cells with predictor columns + `pfr`. Returns (bundle, report)."""
    p = params or get_processing()
    gp = p["gap_model"]
    y_all = make_labels(df["pfr"]) if "pfr" in df else np.full(len(df), np.nan)
    lab = np.isfinite(y_all)
    d = df[lab].reset_index(drop=True)
    X, y = features(d), y_all[lab].astype(int)
    groups = blocks(d)
    spatial = spatial_cv(gp, X, y, groups)
    lro = leave_region_out(gp, d, X, y)
    bundle, cal_info, (X_cal, y_cal) = fit_final(gp, X, y, groups)
    imp = permutation_importance(bundle["raw"], X_cal, y_cal, scoring="roc_auc", n_repeats=5, random_state=0)
    importance = sorted(({"feature": f, "drop_in_auc": round(float(m), 4), "std": round(float(s), 4)}
                         for f, m, s in zip(X.columns, imp.importances_mean, imp.importances_std)),
                        key=lambda r: -r["drop_in_auc"])
    sanity = {}
    for name, (la, lo, expect) in SANITY.items():
        gaps = (df["lat"] - la) ** 2 + ((df["lon"] - lo + 180) % 360 - 180) ** 2 * np.cos(np.radians(la)) ** 2
        i = int(np.argmin(gaps.to_numpy()))
        prob, lbl = predict(bundle, df.iloc[[i]])
        unguarded = bundle["model"].predict_proba(features(df.iloc[[i]]))[:, 1]
        sanity[name] = {"cell": str(df.iloc[i].get("cell_id", "")), "p_ice": round(float(prob[0]), 3),
                        "p_ice_unguarded": round(float(unguarded[0]), 3),
                        "label": None if np.isnan(lbl[0]) else int(lbl[0]), "expected": expect,
                        "distance_deg": round(float(np.sqrt(gaps.iloc[i])), 2)}
    verdict, wins = _verdict(lro, spatial)
    report = {
        "permafrost_model": {
            "task": "ground ice yes/no per 0.5 deg Earth cell",
            "labels": "ESA CCI Permafrost 2021 PFR: ice if >= 0.5, no ice if < 0.1, in between dropped",
            "n_labelled": int(lab.sum()), "n_ice": int(y.sum()), "n_no_ice": int((y == 0).sum()),
            "n_ambiguous_dropped": int((np.isfinite(df["pfr"]) & ~lab).sum()) if "pfr" in df else 0,
            "predictors": PREDICTORS, "excluded_as_leaky": sorted(LEAKY),
            "physics_guard": "P(ice) = 0 where the 1-2.9 m ERA5-Land layer never drops below 0 C "
                             "(mean - range/2 > 273.15 K); model_unguarded = same model without it",
            "share_never_frozen_all_cells": round(float(never_frozen(features(df)).mean()), 3),
            "model": "HistGradientBoostingClassifier " + json.dumps({k: gp[k] for k in ("n_estimators", "max_depth", "learning_rate")}),
            "spatial_block_cv": {"block_deg": BLOCK_DEG, "folds": 5, **spatial},
            "leave_region_out": lro,
            "leave_region_out_wins": wins,
            "calibration": cal_info,
            "permutation_importance": importance,
            "sanity_outside_cci": sanity,
            "verdict": verdict,
            "nn": ({"model": "PyTorch MLP (inputs + 4-d rock embedding) -> 128 -> 64 -> 1, ReLU, dropout 0.2; "
                             "BCE with pos_weight, Adam 1e-3, early stopping on 15% of the training fold; "
                             "same folds and physics guard as the gradient-boosting model, not tuned",
                    "spatial_block_cv": spatial["nn"],
                    "leave_region_out": {r: v["nn"] for r, v in lro.items() if "nn" in v}}
                   if "nn" in spatial else None),
        }
    }
    if "nn" not in spatial:
        report["permafrost_model"]["nn_note"] = "PyTorch not installed: neural-net comparison skipped (optional)."
    if report_path is not None:
        write_report(report, report_path)
    return bundle, report


def write_report(update, path=REPORT_PATH):
    """Merge `update` into output/ml_report.json (other sections, e.g. the gap model, are kept)."""
    path = Path(path)
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current.update(update)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return path
