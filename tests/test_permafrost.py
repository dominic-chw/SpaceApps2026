"""Permafrost classifier: no label leakage, sane predictions, honest report (pipeline/earth_ml/permafrost.py)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.earth_ml import permafrost as pf

ROOT = Path(__file__).resolve().parents[1]
FAST = {"gap_model": {"n_estimators": 60, "max_depth": 4, "learning_rate": 0.1, "cv_block_deg": 10}}


def synthetic_cells(n=2500, seed=0):
    """Cells whose permafrost fraction follows deep-soil temperature (plus noise), like the real world."""
    rng = np.random.default_rng(seed)
    lat = np.concatenate([rng.uniform(26, 84, n), [-77.86, 36.46, -24.08]])
    lon = np.concatenate([rng.uniform(-180, 180, n), [160.74, -116.87, -70.02]])
    t4 = 300 - 0.75 * np.abs(lat) + rng.normal(0, 3, len(lat))
    t4[-3:] = [235.0, 298.0, 292.0]                        # Dry Valleys, Death Valley, Atacama
    df = pd.DataFrame({"cell_id": [f"c{i}" for i in range(len(lat))], "lat": lat, "lon": lon})
    for c in pf.NUMERIC:
        df[c] = rng.normal(0, 1, len(lat))
    for c in ["soil_temperature_level_1_mean", "soil_temperature_level_2_mean", "soil_temperature_level_3_mean",
              "soil_temperature_level_4_mean", "skin_temperature_mean", "lst_day_k", "lst_night_k"]:
        df[c] = t4 + rng.normal(0, 1, len(lat))
    df["lat"] = lat
    df["glim_class"] = rng.choice(["su", "ss", "va", "mt"], len(lat))
    pfr = 1 / (1 + np.exp((t4 - 271) / 1.5))
    pfr[-3:] = np.nan                                     # outside CCI coverage, like the real ones
    df["pfr"] = pfr
    # leaky columns that must be ignored even when present
    df["perm_prob"], df["ice_label"], df["ground_label"] = pfr, (pfr >= 0.5).astype(float), "x"
    return df


@pytest.fixture(scope="module")
def trained():
    df = synthetic_cells()
    bundle, report = pf.train_and_report(df, params=FAST, report_path=None)
    return df, bundle, report["permafrost_model"]


def test_predictors_never_include_label_derived_columns():
    assert not (set(pf.PREDICTORS) & pf.LEAKY)
    for col in ["perm_prob", "ice_label", "ground_label", "pfr", "cci_pfr", "cci_alt_m", "alt_m", "ice_source"]:
        assert col not in pf.PREDICTORS
    X = pf.features(synthetic_cells(50))
    assert list(X.columns) == pf.PREDICTORS
    assert not (set(X.columns) & {"perm_prob", "ice_label", "ground_label", "pfr"})


def test_labels_drop_ambiguous_cells():
    y = pf.make_labels([0.0, 0.05, 0.2, 0.49, 0.5, 0.9, np.nan])
    assert list(y[:2]) == [0, 0] and np.isnan(y[2]) and np.isnan(y[3]) and list(y[4:6]) == [1, 1]
    assert np.isnan(y[6])


def test_sanity_predictions_outside_cci(trained):
    df, bundle, _ = trained
    p, label = pf.predict(bundle, df.tail(3))
    assert label[0] == 1, f"Dry Valleys should be ice (p={p[0]:.2f})"
    assert label[1] == 0 and label[2] == 0, f"Death Valley / Atacama should be ice-free (p={p[1:]})"


def test_report_has_baselines_and_honest_sections(trained):
    _, _, rep = trained
    for block in [rep["spatial_block_cv"]] + [v for v in rep["leave_region_out"].values() if "model" in v]:
        for name in ["model", "majority", "latitude_logistic", "temperature_rule"]:
            assert name in block and {"f1", "balanced_accuracy", "roc_auc"} <= set(block[name])
    assert set(rep["leave_region_out"]) == set(pf.REGIONS)
    assert rep["permutation_importance"] and rep["verdict"]
    assert 0 <= rep["calibration"]["conformal"]["share_uncertain"] <= 1
    assert "perm_prob" in rep["excluded_as_leaky"]


def test_physics_guard_only_uses_predictors_and_zeroes_never_frozen_ground():
    X = pd.DataFrame({"soil_temperature_level_4_mean": [296.0, 270.0, 276.0, np.nan],
                      "soil_temperature_level_4_range": [14.0, 10.0, 8.0, 5.0]})
    assert list(pf.never_frozen(X)) == [True, False, False, False]   # 289 K min / 265 / 272 / unknown
    assert list(pf.guard([0.4, 0.9, 0.7, 0.6], X)) == [0.0, 0.9, 0.7, 0.6]
    assert {"soil_temperature_level_4_mean", "soil_temperature_level_4_range"} <= set(pf.NUMERIC)


def test_conformal_sets_cover_true_class():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 4000)
    p = np.clip(y * 0.7 + rng.normal(0.15, 0.2, len(y)), 0, 1)
    q = pf.conformal_threshold(p[:2000], y[:2000], alpha=0.1)
    no_ice, ice = pf.prediction_sets(p[2000:], q)
    covered = np.where(y[2000:] == 1, ice, no_ice)
    assert covered.mean() >= 0.88


def test_committed_report_matches_real_data_expectations():
    """output/ml_report.json comes from the real CCI + Earth Engine data: the analog sites outside
    CCI coverage must come out right, and the baselines must be in the report."""
    path = ROOT / "output" / "ml_report.json"
    if not path.exists():
        pytest.skip("no ml_report.json yet")
    rep = json.loads(path.read_text(encoding="utf-8"))["permafrost_model"]
    sanity = rep["sanity_outside_cci"]
    assert sanity["Dry Valleys (University Valley)"]["label"] == 1
    assert sanity["Death Valley"]["label"] == 0
    assert sanity["Atacama lowlands (Yungay)"]["label"] == 0
    assert {"majority", "latitude_logistic", "temperature_rule"} <= set(rep["spatial_block_cv"])
