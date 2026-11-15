"""Earth gap-filling (supervised): predict `ice` and `ground_class` where labels are missing.
Predictors: surface temps, elevation, slope, latitude, aridity, GLiM class, bedrock depth.
Labels: ice_label / ground_label columns (CCI, ground ice map, boreholes). Spatial block CV
(GroupKFold over lat/lon blocks). Earth only; filled values get data_quality 'predicted'.
Step 8; plan s10. Hyperparameters from config `processing.gap_model`.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold, cross_val_score

from ..common.config import get_processing

NUMERIC = ["mean_temp_k", "annual_range_k", "elevation_m", "slope_deg", "lat", "aridity",
           "bedrock_depth_m", "perm_prob"]


def _features(df):
    X = df[NUMERIC].copy()
    X = X.loc[:, X.notna().any()]          # drop all-missing predictors (e.g. no bedrock dataset)
    X["glim"] = df["glim_class"].astype("category").cat.codes
    return X


def _model(gp):
    return HistGradientBoostingClassifier(max_iter=gp["n_estimators"], max_depth=gp["max_depth"],
                                          learning_rate=gp["learning_rate"], random_state=0)


def _blocks(df, deg):
    return (np.floor(df["lat"] / deg).astype(int) * 1000 + np.floor(df["lon"] / deg).astype(int))


def fill_gaps(layers, params=None):
    """Return (layers with ice/ground_class + *_q columns, cv_scores dict)."""
    p = params or get_processing()
    gp = p["gap_model"]
    df = layers.copy()
    X = _features(df)
    groups = _blocks(df, gp["cv_block_deg"])
    cv = {}
    targets = {"ice": ("ice_label", lambda s: s.astype(int)),
               "ground_class": ("ground_label", lambda s: s.astype(str))}
    for out, (label, cast) in targets.items():
        known = df[label].notna()
        df[out] = df[label].where(known)
        df[out + "_q"] = np.where(known, "measured", "predicted")
        y = cast(df.loc[known, label])
        n_splits = min(5, groups[known].nunique())
        if y.nunique() < 2 or n_splits < 2:
            cv[out] = None
            df.loc[~known, out] = y.iloc[0] if len(y) else None
            continue
        cv[out] = float(cross_val_score(_model(gp), X[known], y, groups=groups[known],
                                        cv=GroupKFold(n_splits=n_splits)).mean())
        model = _model(gp).fit(X[known], y)
        if (~known).any():
            df.loc[~known, out] = model.predict(X[~known])
    df["ice"] = df["ice"].astype(float).astype(int)
    return df, cv
