"""PyTorch permafrost net (optional): trains on a tiny synthetic set, returns probabilities."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("torch")
from pipeline.earth_ml.permafrost_nn import fit_predict  # noqa: E402


def _toy(n, seed):
    rng = np.random.default_rng(seed)
    t = rng.uniform(250, 300, n)
    X = pd.DataFrame({"skin_temperature_mean": t, "lat": rng.uniform(-80, 80, n),
                      "elevation_m": rng.uniform(0, 4000, n),
                      "glim_class": rng.integers(0, 16, n).astype(float)})
    X.loc[::7, "elevation_m"] = np.nan          # missing numeric
    X.loc[::11, "glim_class"] = np.nan          # missing category
    return X, (t < 271).astype(int)


def test_learns_and_returns_probabilities():
    X, y = _toy(400, 0)
    Xt, yt = _toy(100, 1)
    p = fit_predict(X, y, Xt)
    assert p.shape == (100,) and np.all((p >= 0) & (p <= 1))
    assert ((p >= 0.5) == yt).mean() > 0.85     # the cold = ice signal is easy


def test_unseen_category_ok():
    X, y = _toy(200, 2)
    Xt, _ = _toy(10, 3)
    Xt["glim_class"] = 99.0
    assert np.isfinite(fit_predict(X, y, Xt)).all()
