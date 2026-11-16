"""Physics-informed profile net: physics terms, shapes, and that it learns a toy profile."""
import numpy as np
import pandas as pd
import pytest

from pipeline.earth_ml.profile_nn import DEPTHS_M, physics_terms, violation_rate


def test_physics_zero_for_linear_profile():
    t = np.array([[-5.0, -4.9, -4.88, -4.82, -4.72]])        # gentle geothermal gradient
    c, g, i = physics_terms(t, p_ice=np.array([1.0]))
    assert c[0] == 0 and g[0] == 0 and i[0] == 0
    assert violation_rate(t) == 0


def test_physics_flags_bad_profiles():
    kinked = np.array([[0.0, 0.0, 0.0, 0.0, 5.0]])            # +1 K/m below 5 m
    c, g, _ = physics_terms(kinked)
    assert c[0] > 0 and g[0] > 0
    _, _, i = physics_terms(np.array([[3.0, 3.0, 3.0, 3.0, 3.0]]), p_ice=np.array([1.0]))
    assert i[0] > 0                                            # "ice" at +3 C is inconsistent


def test_fit_predict_shapes_and_learning():
    pytest.importorskip("torch")
    from pipeline.earth_ml.profile_nn import fit_predict
    rng = np.random.default_rng(0)
    n = 500
    t = rng.uniform(-15, 15, n)
    X = pd.DataFrame({"soil_temperature_level_4_mean": t + 273.15, "lat": rng.uniform(30, 80, n),
                      "glim_class": rng.integers(0, 16, n).astype(float)})
    temps = np.column_stack([t + 0.02 * d for d in DEPTHS_M])
    ice = (t < 0).astype(float)
    alt = np.where(t < 0, 0.5 - 0.02 * t, np.nan)
    Y = np.column_stack([ice, alt, temps])
    Y[::5, 2:] = np.nan                                        # missing temperatures are masked
    out = fit_predict(X.iloc[:400], Y[:400], X.iloc[400:])
    assert out["temps_c"].shape == (100, len(DEPTHS_M))
    assert out["p_ice"].shape == out["alt_m"].shape == (100,)
    assert np.sqrt(np.mean((out["temps_c"] - temps[400:]) ** 2)) < 2.0
    assert ((out["p_ice"] >= 0.5) == ice[400:].astype(bool)).mean() > 0.85


def _toy_cells(n, seed):
    rng = np.random.default_rng(seed)
    t = rng.uniform(-15, 15, n)
    X = pd.DataFrame({"soil_temperature_level_4_mean": t + 273.15, "lat": rng.uniform(30, 80, n),
                      "elevation_m": rng.uniform(0, 3000, n), "glim_class": rng.integers(0, 16, n).astype(float)})
    temps = np.column_stack([t + 0.02 * d for d in DEPTHS_M])
    Y = np.column_stack([(t < 0).astype(float), np.where(t < 0, 0.5, np.nan), temps])
    return X, Y


def test_ensemble_spread_and_ood_flag():
    pytest.importorskip("torch")
    from pipeline.earth_ml.profile_nn import fit_ensemble, predict_ensemble
    X, Y = _toy_cells(400, 0)
    bundle = fit_ensemble(X, Y, n_members=3)
    normal = predict_ensemble(bundle, _toy_cells(100, 1)[0])
    assert normal["temps_c"].shape == normal["temps_std"].shape == (100, len(DEPTHS_M))
    assert (normal["temps_std"] >= 0).all()
    assert normal["ood"].mean() < 0.15                      # ~1% by construction; small-sample slack
    weird = _toy_cells(50, 2)[0]
    weird["elevation_m"] = 60000.0                          # far outside anything seen in training
    weird["soil_temperature_level_4_mean"] = 400.0
    out = predict_ensemble(bundle, weird)
    assert out["ood"].mean() > 0.9
    assert out["ood_score"].mean() > normal["ood_score"].mean()


def test_band_temp_interpolates():
    from pipeline.earth_ml.profile_nn import SUB_COLS, band_temp_k
    rec = dict(zip(SUB_COLS, [-10.0, -8.0, -6.0, -6.0, -6.0]))
    assert abs(band_temp_k(rec, 0.5) - (273.15 - 9.0)) < 1e-9
    assert band_temp_k({**rec, SUB_COLS[2]: float("nan")}, 2.0) != band_temp_k(rec, 2.0)   # NaN -> NaN
