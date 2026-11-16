"""Physics-informed multi-task PyTorch net: the Earth subsurface from surface data only (plan idea 1).

One shared trunk, three heads, trained together:
  - ice:   P(permafrost), label from ESA CCI PFR (>= 0.5 ice, < 0.1 no ice, in between masked)
  - alt:   active-layer thickness, m (ESA CCI ALT; only defined where there is permafrost)
  - temps: mean annual ground temperature at 0 / 1 / 2 / 5 / 10 m, deg C (ESA CCI GTD: GST, T1m..T10m)
Inputs are permafrost.features() (ERA5-Land, MODIS LST, terrain, soil, aridity, rock, lat); no CCI value is
ever an input. Missing targets are masked per task, so every labelled cell trains every head it has.

Physics-informed loss (weight `physics_weight`), from steady heat conduction below the annual damping depth:
  1. curvature: the 2-5 m and 5-10 m gradients should agree (near-linear profile at depth);
  2. geothermal bound: |dT/dz| between 5 and 10 m should stay under GRAD_MAX K/m;
  3. ice / temperature consistency: P(ice) high needs T(5 m) <= 0 C, P(ice) low needs T(5 m) >= 0 C.
It needs no labels, so it also regularises cells with missing temperatures.

Baselines in benchmark(): the project heat model (pipeline/physics/heat_model.py: the mean at depth equals the
yearly mean, here ERA5-Land 1-2.9 m), and one gradient-boosting regressor per depth.
Run: python -m pipeline.earth_ml.profile_nn   (writes `profile_nn` into output/ml_report.json)
"""
import time
import warnings
from pathlib import Path

import numpy as np

from . import permafrost as pf
from .permafrost_nn import CAT, N_GLIM, _prep

try:
    import torch
    from torch import nn
except ImportError:  # optional dependency
    torch = None

DEPTHS_M = [0.0, 1.0, 2.0, 5.0, 10.0]
GTD_VARS = ["GST", "T1m", "T2m", "T5m", "T10m"]
GRAD_MAX = 0.1          # K/m; geothermal gradients are ~0.01-0.05, so this only catches unphysical profiles
CURV_TOL = 0.05         # K/m allowed difference between the 2-5 m and 5-10 m gradients
EPOCHS, PATIENCE, BATCH, VAL_FRAC = 120, 12, 256, 0.15


def available():
    return torch is not None


# ---------------------------------------------------------------------------
# Targets (ESA CCI 2021, aggregated to the 0.5 deg grid like build_layers.cci_grid)
# ---------------------------------------------------------------------------

def gtd_grid(var):
    """Mean annual ground temperature (deg C) for one GTD variable on the 360 x 720 grid; cached .npy."""
    from ..earth_library import build_layers as bl
    cache = bl.CCI_DIR / f"gtd_{var.lower()}_0p5deg_mean.npy"
    if cache.exists():
        return np.load(cache)
    path = bl.CCI_DIR / "GTD_2021.nc"
    if not path.exists():
        return None
    import h5py
    with h5py.File(path, "r") as h:
        ds = h[var]
        fill = int(ds.attrs["_FillValue"][0])
        scale = float(ds.attrs["scale_factor"][0])
        offset = float(ds.attrs["add_offset"][0])
        lats = h["lat"][:]
        step = int(round(bl.GRID / 0.01))
        n_rows, n_cols = ds.shape[1] // step, ds.shape[2] // step
        out = np.full((n_rows, n_cols), np.nan)
        north_first = lats[0] > lats[-1]
        for r in range(n_rows):
            r0 = r * step if north_first else ds.shape[1] - (r + 1) * step
            block = ds[0, r0:r0 + step, :].astype(float)
            block[block == fill] = np.nan
            cells = block.reshape(step, n_cols, step).transpose(1, 0, 2).reshape(n_cols, -1)
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                out[r] = np.nanmean(cells, axis=1) * scale + offset
    full = np.full((int(180 / bl.GRID), int(360 / bl.GRID)), np.nan)
    top = int(round((90 - bl.CCI_TOP) / bl.GRID))
    full[top:top + out.shape[0]] = out
    np.save(cache, full)
    return full


def targets(df):
    """(n, 7) float array: ice (0/1/NaN), alt_m, then temperatures at DEPTHS_M (deg C). NaN = no label."""
    from ..earth_library import build_layers as bl
    lat, lon = df["lat"].to_numpy(float), df["lon"].to_numpy(float)
    pfr = bl.lookup(bl.cci_grid("PFR"), lat, lon)
    alt = bl.lookup(bl.cci_grid("ALT"), lat, lon)
    alt = np.where((alt > 0) & (alt < 20), alt, np.nan)          # ALT only means something in permafrost
    temps = [bl.lookup(gtd_grid(v), lat, lon) for v in GTD_VARS]
    return np.column_stack([pf.make_labels(pfr), alt] + temps)


def features(df):
    """permafrost.features() with |latitude|: yearly-mean ground temperature does not care which hemisphere a
    cell is in, and every CCI label is in the north, so signed latitude would make the whole Southern
    Hemisphere look out of distribution. Identical to permafrost.features() for the training cells."""
    X = pf.features(df)
    X["lat"] = X["lat"].abs()
    return X


# ---------------------------------------------------------------------------
# Physics
# ---------------------------------------------------------------------------

def physics_terms(temps_c, p_ice=None, xp=np):
    """Per-cell physics violations for temperatures at DEPTHS_M (deg C). Works on numpy or torch.
    Returns (curvature, gradient, ice_consistency); each is 0 for a physically fine profile."""
    relu = (lambda a: a.clamp(min=0)) if xp is not np else (lambda a: np.maximum(a, 0))
    absf = (lambda a: a.abs()) if xp is not np else np.abs
    g25 = (temps_c[:, 3] - temps_c[:, 2]) / 3.0
    g510 = (temps_c[:, 4] - temps_c[:, 3]) / 5.0
    curvature = relu(absf(g510 - g25) - CURV_TOL)
    gradient = relu(absf(g510) - GRAD_MAX)
    if p_ice is None:
        ice = curvature * 0
    else:
        t5 = temps_c[:, 3]
        ice = p_ice * relu(t5) + (1 - p_ice) * relu(-t5)
    return curvature, gradient, ice


def violation_rate(temps_c):
    """Share of cells whose profile breaks the curvature or geothermal rule (no ice term)."""
    c, g, _ = physics_terms(np.asarray(temps_c, float))
    return float(np.mean((c > 0) | (g > 0)))


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class ProfileNet(nn.Module if torch else object):
    def __init__(self, n_numeric, n_temps=len(DEPTHS_M), emb_dim=4):
        super().__init__()
        self.emb = nn.Embedding(N_GLIM + 1, emb_dim)
        self.trunk = nn.Sequential(
            nn.Linear(n_numeric + emb_dim, 128), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(128, 128), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(128, 64), nn.ReLU())
        self.ice = nn.Linear(64, 1)
        self.alt = nn.Linear(64, 1)
        self.temps = nn.Linear(64, n_temps)

    def embed(self, x_num, x_cat):
        """64-d hidden representation (used by the out-of-distribution check)."""
        return self.trunk(torch.cat([x_num, self.emb(x_cat)], dim=1))

    def forward(self, x_num, x_cat):
        h = self.embed(x_num, x_cat)
        return self.ice(h).squeeze(1), self.alt(h).squeeze(1), self.temps(h)


def _masked_mse(pred, y, mask):
    n = mask.sum()
    return ((pred - y) ** 2 * mask).sum() / n if n > 0 else pred.sum() * 0


def fit_predict(X_tr, Y_tr, X_te, physics_weight=0.1, seed=0, return_history=False):
    """Train on (X_tr, Y_tr = targets()), predict X_te. Returns {'p_ice', 'alt_m', 'temps_c' (n, 5)}."""
    member = train(X_tr, Y_tr, physics_weight=physics_weight, seed=seed)
    out = predict_member(member, X_te)
    out.pop("embedding")
    if return_history:
        out["history"] = member["history"]
    return out


def predict_member(member, X):
    """One trained net -> {'p_ice', 'alt_m', 'temps_c', 'embedding'} for the rows of X."""
    (tn, tc), _ = _prep(X, member["stats"])
    net, mu, sd = member["net"], member["mu"], member["sd"]
    net.eval()
    with torch.no_grad():
        h = net.embed(tn, tc)
        ice_logit, alt, temps = net.ice(h).squeeze(1), net.alt(h).squeeze(1), net.temps(h)
    return {"p_ice": torch.sigmoid(ice_logit).numpy().astype(float),
            "alt_m": (alt.numpy() * sd[0] + mu[0]).astype(float),
            "temps_c": (temps.numpy() * sd[1:] + mu[1:]).astype(float),
            "embedding": h.numpy().astype(float)}


def train(X_tr, Y_tr, physics_weight=0.1, seed=0):
    """Train one net. Returns {'net', 'stats', 'mu', 'sd', 'history', 'val_index'}."""
    if torch is None:
        raise ImportError("torch is not installed")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    (xn, xc), stats = _prep(X_tr)
    Y = np.asarray(Y_tr, float)
    mask = np.isfinite(Y)
    # Standardise the regression targets on the training fold (ice stays 0/1).
    mu = np.where(mask[:, 1:].any(0), np.nanmean(np.where(mask[:, 1:], Y[:, 1:], np.nan), 0), 0.0)
    sd = np.nanstd(np.where(mask[:, 1:], Y[:, 1:], np.nan), 0)
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, 1.0)
    Yn = np.column_stack([Y[:, :1], (Y[:, 1:] - mu) / sd])
    Yt = torch.tensor(np.nan_to_num(Yn), dtype=torch.float32)
    Mt = torch.tensor(mask, dtype=torch.float32)
    mu_t = torch.tensor(mu[1:], dtype=torch.float32)       # temperatures only
    sd_t = torch.tensor(sd[1:], dtype=torch.float32)

    ice_y = Y[:, 0][mask[:, 0]]
    pos = float(ice_y.sum())
    neg = float(len(ice_y) - pos)
    bce = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(neg / pos if pos > 0 else 1.0), reduction="none")

    def loss_fn(out, b):
        ice_logit, alt, temps = out
        m = Mt[b]
        l_ice = (bce(ice_logit, Yt[b, 0]) * m[:, 0]).sum() / m[:, 0].sum().clamp(min=1)
        l_alt = _masked_mse(alt, Yt[b, 1], m[:, 1])
        l_t = _masked_mse(temps, Yt[b, 2:], m[:, 2:])
        sup = l_ice + 0.5 * l_alt + l_t
        if physics_weight <= 0:
            return sup, sup
        temps_c = temps * sd_t + mu_t
        c, g, i = physics_terms(temps_c, torch.sigmoid(ice_logit), xp=torch)
        return sup + physics_weight * (c.mean() + g.mean() + i.mean()), sup

    idx = rng.permutation(len(Y))
    n_val = max(1, int(len(Y) * VAL_FRAC)) if len(Y) >= 20 else 0
    val, tr = idx[:n_val], idx[n_val:]
    net = ProfileNet(xn.shape[1])
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    best, best_state, waited, history = np.inf, None, 0, []
    for _ in range(EPOCHS):
        net.train()
        for b in np.array_split(rng.permutation(tr), max(1, len(tr) // BATCH)):
            opt.zero_grad()
            loss_fn(net(xn[b], xc[b]), b)[0].backward()
            opt.step()
        if not n_val:
            continue
        net.eval()
        with torch.no_grad():
            v = loss_fn(net(xn[val], xc[val]), val)[1].item()     # early stopping on the supervised loss
        history.append(v)
        if v < best - 1e-4:
            best, waited = v, 0
            best_state = {k: t.clone() for k, t in net.state_dict().items()}
        else:
            waited += 1
            if waited >= PATIENCE:
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    return {"net": net, "stats": stats, "mu": mu, "sd": sd, "history": history, "train_index": tr}


# ---------------------------------------------------------------------------
# Deep ensemble + out-of-distribution ("extrapolating") flag
# ---------------------------------------------------------------------------
# Each member gets its own seed (init, batches, early-stopping split). Spread across members = uncertainty.
# OOD: Mahalanobis distance of a cell's hidden representation from the training cloud (Ledoit-Wolf
# covariance), averaged over members. Threshold = OOD_QUANTILE of the scores of calibration cells that no
# member trained on, so about 1% of normal cells are flagged by construction.

N_MEMBERS = 5
OOD_QUANTILE = 0.99
CAL_FRAC = 0.10


def _mahalanobis_fit(emb):
    from sklearn.covariance import LedoitWolf
    lw = LedoitWolf().fit(emb)
    return lw.location_, lw.precision_


def _mahalanobis(emb, fit):
    mu, prec = fit
    d = emb - mu
    return np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", d, prec, d), 0))


def fit_ensemble(X, Y, n_members=N_MEMBERS, physics_weight=0.1, seed=0, groups=None):
    """Train the ensemble on (X, Y) minus a calibration slice (whole 10 deg blocks when `groups` is given),
    then set the OOD threshold on that slice. Returns a bundle for predict_ensemble()."""
    rng = np.random.default_rng(seed)
    n = len(X)
    if groups is not None and len(np.unique(groups)) >= 10:
        ub = np.unique(groups)
        cal = np.isin(groups, rng.choice(ub, size=max(1, int(len(ub) * CAL_FRAC)), replace=False))
    else:
        cal = np.zeros(n, bool)
        cal[rng.choice(n, size=max(1, int(n * CAL_FRAC)), replace=False)] = True
    Xf, Yf = X[~cal], Y[~cal]
    members = []
    for k in range(n_members):
        m = train(Xf, Yf, physics_weight=physics_weight, seed=seed + k)
        emb = predict_member(m, Xf.iloc[m["train_index"]])["embedding"]
        m["ood_fit"] = _mahalanobis_fit(emb)
        members.append(m)
    bundle = {"members": members, "threshold": np.inf}
    cal_scores = predict_ensemble(bundle, X[cal])["ood_score"]
    bundle["threshold"] = float(np.quantile(cal_scores, OOD_QUANTILE))
    bundle["calibration_cells"] = int(cal.sum())
    return bundle


def predict_ensemble(bundle, X):
    """Mean and spread over members, plus the OOD score and flag."""
    outs = [predict_member(m, X) for m in bundle["members"]]
    temps = np.stack([o["temps_c"] for o in outs])
    score = np.mean([_mahalanobis(o["embedding"], m["ood_fit"]) for o, m in zip(outs, bundle["members"])], axis=0)
    return {"p_ice": np.mean([o["p_ice"] for o in outs], axis=0),
            "p_ice_std": np.std([o["p_ice"] for o in outs], axis=0),
            "alt_m": np.mean([o["alt_m"] for o in outs], axis=0),
            "temps_c": temps.mean(axis=0), "temps_std": temps.std(axis=0),
            "ood_score": score, "ood": score > bundle["threshold"]}


def benchmark_ensemble(df, physics_weight=0.1):
    """Leave-region-out: does the ensemble beat one net, is its spread honest, and does the OOD flag
    find the cells where the model is wrong? Also: how much of the no-label world gets flagged."""
    Y_all = targets(df)
    has = np.isfinite(Y_all).any(axis=1)
    d = df[has].reset_index(drop=True)
    X, Y = features(d), Y_all[has]
    groups = pf.blocks(d)
    out = {}
    for reg in pf.REGIONS:
        test = pf.in_region(d, reg)
        if test.sum() < 20:
            continue
        bundle = fit_ensemble(X[~test], Y[~test], physics_weight=physics_weight, groups=groups[~test])
        ens = predict_ensemble(bundle, X[test])
        single = predict_member(bundle["members"][0], X[test])
        yt = Y[test][:, 2:]
        ok = np.isfinite(yt)
        err = np.abs(ens["temps_c"] - yt)
        rmse = lambda e: round(float(np.sqrt(np.nanmean(np.where(ok, e, np.nan) ** 2))), 3)
        cell_err = np.sqrt(np.nanmean(np.where(ok, err, np.nan) ** 2, axis=1))
        flag = ens["ood"]
        out[reg] = {
            "n": int(test.sum()),
            "rmse_all_depths_single_net": rmse(np.abs(single["temps_c"] - yt)),
            "rmse_all_depths_ensemble": rmse(err),
            "within_2_std": round(float(np.mean((err <= 2 * ens["temps_std"])[ok])), 3),
            "spread_error_corr": round(float(np.corrcoef(ens["temps_std"].mean(1), cell_err)[0, 1]), 3)
            if np.isfinite(cell_err).all() else None,
            "share_flagged": round(float(flag.mean()), 3),
            "rmse_flagged": round(float(np.sqrt(np.nanmean(cell_err[flag] ** 2))), 3) if flag.any() else None,
            "rmse_not_flagged": round(float(np.sqrt(np.nanmean(cell_err[~flag] ** 2))), 3) if (~flag).any() else None,
        }
    # The cells with no CCI labels at all (Southern Hemisphere, tropics, Antarctica): flagged share by area.
    bundle = fit_ensemble(X, Y, physics_weight=physics_weight, groups=groups)
    rest = df[~has].reset_index(drop=True)
    unl = {}
    if len(rest):
        ood = predict_ensemble(bundle, features(rest))["ood"]
        lat = rest["lat"].to_numpy(float)
        zones = {"Antarctica (< 60 S)": lat < -60, "Southern mid-latitudes (60-25 S)": (lat >= -60) & (lat < -25),
                 "Tropics (25 S-25 N)": (lat >= -25) & (lat < 25), "North, outside CCI cover": lat >= 25}
        unl = {z: {"n": int(m.sum()), "share_flagged": round(float(ood[m].mean()), 3)} for z, m in zones.items() if m.any()}
    return {"members": N_MEMBERS, "ood_method": "Mahalanobis distance in the 64-d hidden layer (Ledoit-Wolf), "
            f"mean over members; flagged above the {OOD_QUANTILE:.0%} quantile of held-out calibration blocks",
            "leave_region_out": out, "unlabelled_world": unl}


# ---------------------------------------------------------------------------
# Production: subsurface temperatures for every Earth cell (build_layers) + the web export
# ---------------------------------------------------------------------------

SUB_COLS = [f"sub_t_{d:g}m" for d in DEPTHS_M]


def subsurface_columns(df, model_path=None):
    """For every Earth cell: ground temperature (deg C) at DEPTHS_M and where it comes from.
      sub_source = "cci"          ESA CCI GTD (inside 25-85 N)
                   "nn"           ensemble mean (outside CCI, in distribution)
                   "extrapolated" ensemble is out of distribution: values NaN, the heat model is used instead
    Also sub_t_std (ensemble spread, deg C, mean over depths), sub_ood_score. Returns (DataFrame, summary)."""
    import pandas as pd
    Y_all = targets(df)
    has = np.isfinite(Y_all[:, 2:]).all(axis=1)
    X = features(df)
    bundle = fit_ensemble(X[has], Y_all[has], groups=pf.blocks(df[has]))
    ens = predict_ensemble(bundle, X)
    temps = np.where(has[:, None], Y_all[:, 2:], ens["temps_c"])
    source = np.where(has, "cci", np.where(ens["ood"], "extrapolated", "nn")).astype(object)
    temps[source == "extrapolated"] = np.nan
    out = pd.DataFrame(np.round(temps, 2), columns=SUB_COLS, index=df.index)
    out["sub_t_std"] = np.round(np.where(has, np.nan, ens["temps_std"].mean(axis=1)), 3)
    out["sub_ood_score"] = np.round(ens["ood_score"], 3)
    out["sub_source"] = source
    if model_path is not None:
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"members": [{k: v for k, v in m.items() if k not in ("net", "train_index", "history")} | {"state": m["net"].state_dict()}
                                for m in bundle["members"]], "threshold": bundle["threshold"]}, model_path)
    summary = {"cells": int(len(df)), "from_cci": int((source == "cci").sum()), "from_nn": int((source == "nn").sum()),
               "extrapolated": int((source == "extrapolated").sum()),
               "ood_threshold": round(bundle["threshold"], 3), "members": len(bundle["members"])}
    return out, summary


def band_temp_k(rec, depth_m):
    """Ground temperature (K) at depth_m from a layers record's sub_t_* columns (linear in depth), or NaN."""
    vals = [rec.get(c) for c in SUB_COLS]
    if any(v is None or v != v for v in vals):
        return float("nan")
    return float(np.interp(depth_m, DEPTHS_M, vals)) + 273.15


def export_for_web(place_ids, layers_path, out_path, fake=False):
    """output/earth_subsurface.json: {depths_m, places: {id: {t: [...], sd, src}}} for the Earth places
    in earth_library.json. src: cci | nn | extrapolated."""
    import json
    import pandas as pd
    layers = pd.read_parquet(layers_path)
    if "sub_source" not in layers:
        return None
    layers = layers[layers["cell_id"].isin(set(place_ids))]
    places = {}
    for rec in layers.to_dict("records"):
        t = [None if v != v else round(float(v), 1) for v in (rec[c] for c in SUB_COLS)]
        if rec["sub_source"] == "extrapolated":
            # What matching uses there: the heat model (mean at every depth = yearly surface mean).
            m = rec.get("mean_temp_k")
            t = [None if m is None or m != m else round(float(m) - 273.15, 1)] * len(DEPTHS_M)
        sd = rec.get("sub_t_std")
        places[rec["cell_id"]] = {"t": t, "src": rec["sub_source"],
                                  **({"sd": round(float(sd), 2)} if sd == sd and sd is not None else {})}
    data = {"fake": fake, "depths_m": DEPTHS_M, "places": places}
    Path(out_path).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Benchmark: physics-informed net vs plain net vs gradient boosting vs heat model
# ---------------------------------------------------------------------------

def _heat_model_temps(X):
    """Project heat model: mean at every depth = yearly mean (heat_model.temps_at_depth). ERA5-Land 1-2.9 m."""
    t = X["soil_temperature_level_4_mean"].to_numpy(float) - 273.15
    return np.repeat(t[:, None], len(DEPTHS_M), axis=1)


def _gbm_temps(gp, X_tr, Y_tr, X_te):
    from sklearn.ensemble import HistGradientBoostingRegressor
    out = np.full((len(X_te), len(DEPTHS_M)), np.nan)
    cat = [pf.PREDICTORS.index(c) for c in pf.CATEGORICAL]
    for j in range(len(DEPTHS_M)):
        y = Y_tr[:, 2 + j]
        ok = np.isfinite(y)
        m = HistGradientBoostingRegressor(max_iter=gp["n_estimators"], max_depth=gp["max_depth"],
                                          learning_rate=gp["learning_rate"], categorical_features=cat,
                                          random_state=0).fit(X_tr[ok], y[ok])
        out[:, j] = m.predict(X_te)
    return out


def _score(Y, preds):
    """Per-depth RMSE (deg C), ALT RMSE, ice F1 / AUC, and the physics violation rate of the predictions."""
    res = {}
    for j, d in enumerate(DEPTHS_M):
        ok = np.isfinite(Y[:, 2 + j])
        res[f"rmse_{d:g}m"] = round(float(np.sqrt(np.mean((preds["temps_c"][ok, j] - Y[ok, 2 + j]) ** 2))), 3) \
            if ok.any() else None
    if "alt_m" in preds:
        ok = np.isfinite(Y[:, 1])
        res["alt_rmse_m"] = round(float(np.sqrt(np.mean((preds["alt_m"][ok] - Y[ok, 1]) ** 2))), 3) if ok.any() else None
    if "p_ice" in preds:
        ok = np.isfinite(Y[:, 0])
        y = Y[ok, 0].astype(int)
        res["ice"] = pf.metrics(y, (preds["p_ice"][ok] >= 0.5).astype(int), preds["p_ice"][ok])
    res["physics_violation_rate"] = round(violation_rate(preds["temps_c"]), 4)
    return res


def _all_models(gp, X_tr, Y_tr, X_te, physics_weight):
    t = time.time()
    pinn = fit_predict(X_tr, Y_tr, X_te, physics_weight=physics_weight)
    t_pinn = time.time() - t
    plain = fit_predict(X_tr, Y_tr, X_te, physics_weight=0.0)
    return {"physics_informed_nn": pinn, "plain_nn": plain,
            "gbm_per_depth": {"temps_c": _gbm_temps(gp, X_tr, Y_tr, X_te)},
            "heat_model": {"temps_c": _heat_model_temps(X_te)}}, t_pinn


def benchmark(df, params=None, physics_weight=0.1, n_splits=5):
    """Spatial block CV + leave-region-out on every cell with any CCI target. Returns the report section."""
    from sklearn.model_selection import GroupKFold
    from ..common.config import get_processing
    gp = (params or get_processing())["gap_model"]
    Y_all = targets(df)
    has = np.isfinite(Y_all).any(axis=1)
    d = df[has].reset_index(drop=True)
    X, Y = features(d), Y_all[has]
    groups = pf.blocks(d)

    names = ["physics_informed_nn", "plain_nn", "gbm_per_depth", "heat_model"]
    oof = {n: {"temps_c": np.full((len(Y), len(DEPTHS_M)), np.nan)} for n in names}
    for n in names[:2]:
        oof[n].update(p_ice=np.full(len(Y), np.nan), alt_m=np.full(len(Y), np.nan))
    secs = 0.0
    for tr, te in GroupKFold(n_splits=n_splits).split(X, Y, groups):
        res, t = _all_models(gp, X.iloc[tr], Y[tr], X.iloc[te], physics_weight)
        secs += t
        for n, p in res.items():
            for k, v in p.items():
                oof[n][k][te] = v
    spatial = {n: _score(Y, oof[n]) for n in names}

    lro = {}
    for reg in pf.REGIONS:
        test = pf.in_region(d, reg)
        if test.sum() < 20:
            lro[reg] = {"note": f"only {int(test.sum())} cells; skipped"}
            continue
        res, _ = _all_models(gp, X[~test], Y[~test], X[test], physics_weight)
        lro[reg] = {n: _score(Y[test], p) for n, p in res.items()}

    return {
        "task": "subsurface from surface data: P(ice), active-layer thickness, mean annual ground temp at "
                + ", ".join(f"{d:g}" for d in DEPTHS_M) + " m",
        "labels": "ESA CCI Permafrost 2021 (PFR, ALT, GTD GST/T1m/T2m/T5m/T10m), 0.5 deg cell means, 25-85 N",
        "n_cells": int(len(Y)),
        "n_labelled": {"ice": int(np.isfinite(Y[:, 0]).sum()), "alt": int(np.isfinite(Y[:, 1]).sum()),
                       **{f"t_{d:g}m": int(np.isfinite(Y[:, 2 + j]).sum()) for j, d in enumerate(DEPTHS_M)}},
        "model": "PyTorch multi-task MLP: shared trunk (inputs + 4-d rock embedding) -> 128 -> 128 -> 64, ReLU, "
                 "dropout 0.2; heads ice (BCE), ALT and 5 depth temperatures (masked MSE); Adam, early stopping",
        "physics_loss": {"weight": physics_weight, "terms": [
            f"curvature: |grad(5-10 m) - grad(2-5 m)| <= {CURV_TOL} K/m",
            f"geothermal: |grad(5-10 m)| <= {GRAD_MAX} K/m",
            "ice consistency: P(ice) * relu(T5m) + (1 - P(ice)) * relu(-T5m)"]},
        "baselines": {"plain_nn": "same net, physics weight 0",
                      "gbm_per_depth": "HistGradientBoostingRegressor per depth",
                      "heat_model": "pipeline/physics/heat_model.py: mean at depth = yearly mean (ERA5-Land 1-2.9 m)"},
        "spatial_block_cv": {"block_deg": pf.BLOCK_DEG, "folds": n_splits, **spatial},
        "leave_region_out": lro,
        "train_seconds_physics_nn_cv": round(secs, 1),
    }


def load_cells():
    """Earth Engine 0.5 deg grid + GLiM rock class (the EE CSVs carry no rock column)."""
    from ..earth_library import build_layers as bl
    df = bl.load_ee()
    if "glim_class" not in df:
        try:
            df["glim_class"] = bl.lookup(bl.glim_grid(), df["lat"].to_numpy(float), df["lon"].to_numpy(float))
        except Exception as e:  # no GLiM on disk: the rock embedding then sees "missing" everywhere
            warnings.warn(f"no GLiM rock class: {e}")
    return df


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Benchmark the physics-informed subsurface net and its ensemble.")
    ap.add_argument("--ensemble-only", action="store_true", help="skip the single-net benchmark (~15 min)")
    args = ap.parse_args(argv)
    if not available():
        raise SystemExit("torch is not installed (pip install torch --index-url https://download.pytorch.org/whl/cpu)")
    df = load_cells()
    if not args.ensemble_only:
        report = benchmark(df)
        pf.write_report({"profile_nn": report})
        cv = report["spatial_block_cv"]
        for n in ("physics_informed_nn", "plain_nn", "gbm_per_depth", "heat_model"):
            print(f"{n:20s}", {k: v for k, v in cv[n].items() if k != "ice"})
    ens = benchmark_ensemble(df)
    pf.write_report({"profile_ensemble": ens})
    for reg, r in ens["leave_region_out"].items():
        print(reg, r)
    print(ens["unlabelled_world"])


if __name__ == "__main__":
    main()
