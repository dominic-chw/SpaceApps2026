"""Learn match weights from the known Mars-Earth analog pairs, and test them honestly.

Model
-----
One multiplier per feature, SHARED across depth bands, on top of the hand-set
weights/default.json:  w[band][f] = default[band][f] * mult[f],  mult in [0.25, 4].
Ten numbers for twelve pairs: deliberately small, and an L2 penalty on log(mult)
pulls them towards 1 (= the hand-set weights) unless the data says otherwise.

Loss
----
mean over training pairs of log(rank of the agreed Earth site) + LAMBDA * mean(log(mult)^2).
Ranks are the app's ranks (closest first, places kept 200 km apart), exactly as
evaluate.pair_ranks computes them. log(rank) rewards moving a site from 200 to 20
as much as from 20 to 2.

Optimiser
---------
Ranks are step functions of the weights (no gradient), so: N_DRAWS random draws of
log(mult) inside the bounds (plus mult = 1), then Nelder-Mead from the best draw.

Honest evaluation
-----------------
Leave-one-group-out: all pairs of one Mars site are held out together (Phoenix x3,
Viking 1 x2), weights are learned on the rest, and only the held-out pairs are ranked.
The learned weights are reported next to the hand-set weights and random ranking.
LAMBDA, N_DRAWS and the bounds are fixed here in advance; they were not tuned on the result.

In:  Mars / Earth feature tables per band, known_pairs.csv, weights/default.json.
Out: weights/learned.json and the `learned_weights` block of output/validation.json.
Run: python -m pipeline.validation.learn_weights
"""
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from pipeline.matching import match, normalize
from pipeline.validation import evaluate

ROOT = Path(__file__).resolve().parents[2]
LEARNED_PATH = ROOT / "weights" / "learned.json"
BOUNDS = (0.25, 4.0)
LAMBDA = 0.05          # L2 on log(mult); fixed in advance
N_DRAWS = 300
SEED = 0


# ---------------------------------------------------------------------------
# Fast, exact re-implementation of the rank of one pair's Earth site
# ---------------------------------------------------------------------------

def spacing_matrix(lats, lons, spacing_km=evaluate.SPACING_KM):
    """near[i, j] = True when Earth places i and j are closer than spacing_km."""
    lat, lon = np.radians(lats)[:, None], np.radians(lons)[:, None]
    h = (np.sin((lat - lat.T) / 2) ** 2 + np.cos(lat) * np.cos(lat.T) * np.sin((lon - lon.T) / 2) ** 2)
    return 2 * match.EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1))) < spacing_km


def best_target_rank(distances, near, is_target, lats, lons):
    """Same answer as evaluate.spaced_ranks(...)[0][is_target].min(), without ranking every place.

    Walk places from closest to farthest; a place opens a new rank unless it is within the
    spacing of a place that already did ("head"). A target that is not a head takes the rank
    of its nearest head, like spaced_ranks.
    """
    order = np.argsort(np.where(np.isnan(distances), np.inf, distances), kind="stable")
    covered = np.zeros(len(distances), dtype=bool)
    heads, best, left = [], None, int(is_target.sum())
    for i in order:
        if covered[i]:
            if is_target[i]:
                h = np.array(heads)
                gaps = match.distance_km(lats[i], lons[i], lats[h], lons[h])
                r = int(np.argmin(gaps)) + 1
                best = r if best is None else min(best, r)
                left -= 1
        else:
            heads.append(i)
            covered |= near[i]
            if is_target[i]:
                best = len(heads) if best is None else min(best, len(heads))
                left -= 1
        if left == 0:
            break        # every target has its rank; later places can't change it
    return best


# ---------------------------------------------------------------------------
# Pairs -> precomputed pieces, so one evaluation is cheap
# ---------------------------------------------------------------------------

def prepare_pairs(mars_tables, earth_tables, pairs, feature_file=None, base_weights=None):
    """Per usable pair: the per-feature differences to every Earth place, its targets, etc."""
    feature_file = feature_file or normalize.load_feature_file()
    base_weights = base_weights or match.load_weights()
    all_features = list(feature_file["features"])
    prepared, near_cache, items = {}, {}, []
    for _, pair in pairs.iterrows():
        band = str(pair["depth_band"]).strip()
        if band not in mars_tables or band not in earth_tables:
            continue
        if band not in prepared:
            prepared[band] = match.prepare_band(mars_tables[band], earth_tables[band], band,
                                                feature_file, base_weights)
            p = prepared[band]
            near_cache[band] = spacing_matrix(p["earth_lats"], p["earth_lons"])
        p = prepared[band]
        targets = evaluate.pair_targets(pair, p)
        if not targets:
            continue
        m = evaluate.nearest_mars_row(p["mars"], pair["mars_lat"], pair["mars_lon"])
        diffs = match.feature_differences(p["mars_grid"][m], p["earth_grid"], p["scales"], p["is_category"])
        items.append({
            "pair_id": pair["pair_id"], "group": str(pair["mars_name"]).strip(), "band": band,
            "diffs": diffs, "present": ~np.isnan(diffs), "diffs0": np.nan_to_num(diffs),
            "base": p["weights"], "feature_index": np.array([all_features.index(n) for n in p["names"]]),
            "min_coverage": p["confidence_settings"]["min_coverage"],
            "is_target": np.isin(np.asarray(p["site_ids"]), list(targets)),
            "near": near_cache[band], "lats": p["earth_lats"], "lons": p["earth_lons"],
        })
    return items, all_features


def pair_rank(item, mult):
    """Rank of the pair's Earth site with multipliers `mult` (cell_distances maths, vectorised)."""
    w = item["base"] * mult[item["feature_index"]]
    used = item["present"] @ w
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = np.where(used > 0, (item["diffs0"] * item["present"]) @ w / used, np.nan)
    total = w[w > 0].sum()
    if total > 0:
        enough = used / total >= item["min_coverage"]
        if enough.any():
            dist = np.where(enough, dist, np.nan)
    return best_target_rank(dist, item["near"], item["is_target"], item["lats"], item["lons"])


def loss(log_mult, items):
    log_mult = np.clip(log_mult, np.log(BOUNDS[0]), np.log(BOUNDS[1]))
    mult = np.exp(log_mult)
    ranks = [pair_rank(it, mult) for it in items]
    return float(np.mean(np.log(ranks))) + LAMBDA * float(np.mean(log_mult ** 2))


def fit(items, n_features, n_draws=N_DRAWS, seed=SEED):
    """Random search + Nelder-Mead in log space. Returns multipliers (array, within BOUNDS)."""
    rng = np.random.default_rng(seed)
    lo, hi = np.log(BOUNDS[0]), np.log(BOUNDS[1])
    draws = np.vstack([np.zeros(n_features), rng.uniform(lo, hi, size=(n_draws, n_features))])
    scores = [loss(d, items) for d in draws]
    start = draws[int(np.argmin(scores))]
    res = minimize(loss, start, args=(items,), method="Nelder-Mead",
                   options={"maxfev": 600, "xatol": 1e-3, "fatol": 1e-4})
    best = res.x if res.fun <= min(scores) else start
    return np.exp(np.clip(best, lo, hi))


def leave_one_group_out(items, n_features, n_draws=N_DRAWS):
    """Learn on all other Mars sites, rank the held-out site's pairs. Returns rows per pair."""
    rows = []
    groups = sorted({it["group"] for it in items})
    for g in groups:
        train = [it for it in items if it["group"] != g]
        test = [it for it in items if it["group"] == g]
        assert not ({id(t) for t in train} & {id(t) for t in test})
        mult = fit(train, n_features, n_draws)
        for it in test:
            rows.append({"pair_id": int(it["pair_id"]), "group": g, "band": it["band"],
                         "learned_rank": pair_rank(it, mult), "hand_rank": pair_rank(it, np.ones(n_features)),
                         "n_places": None})
    return rows


def summary(ranks, n_places, seed=SEED):
    s = evaluate.summarize(ranks, n_places, 1000, np.random.default_rng(seed))
    return {k: s[k] for k in ("n_pairs", "recall_at_1", "recall_at_3", "recall_at_10", "mean_rank",
                              "random_recall_at_1", "random_recall_at_3", "random_recall_at_10",
                              "random_mean_rank") if k in s}


def distinct_places(item):
    """Distinct places after the 200 km rule with the hand-set weights, exactly as
    evaluate.pair_ranks counts them, so the random baseline matches validation.json."""
    mult = np.ones(int(item["feature_index"].max()) + 1)
    w = item["base"] * mult[item["feature_index"]]
    used = item["present"] @ w
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = np.where(used > 0, (item["diffs0"] * item["present"]) @ w / used, np.nan)
    total = w[w > 0].sum()
    enough = used / total >= item["min_coverage"]
    if enough.any():
        dist = np.where(enough, dist, np.nan)
    return evaluate.spaced_ranks(dist, item["lats"], item["lons"])[1]


def loo_summaries(items, rows):
    places = {int(it["pair_id"]): distinct_places(it) for it in items}
    for r in rows:
        r["n_places"] = places[r["pair_id"]]
    n = [r["n_places"] for r in rows]
    return (summary([r["learned_rank"] for r in rows], n), summary([r["hand_rank"] for r in rows], n))


def learned_weight_table(mult, all_features, base_weights):
    return {band: {f: round(float(w) * float(mult[all_features.index(f)]), 3) for f, w in bw.items()}
            for band, bw in base_weights.items()}


def run(mars_tables, earth_tables, pairs, out_path=LEARNED_PATH, n_draws=N_DRAWS):
    """Full fit + LOO report. Returns (learned weights {band: {feature: w}}, report dict)."""
    base = match.load_weights()
    items, all_features = prepare_pairs(mars_tables, earth_tables, pairs, base_weights=base)
    if len(items) < 3:
        return None, {"note": f"only {len(items)} usable pairs; weights not learned"}
    n = len(all_features)
    rows = leave_one_group_out(items, n, n_draws)
    learned, hand = loo_summaries(items, rows)
    # Rule fixed in advance: adopt only if LOO mean rank is lower AND recall@10 is not worse.
    adopt = learned["mean_rank"] < hand["mean_rank"] and learned["recall_at_10"] >= hand["recall_at_10"]

    mult = fit(items, n, n_draws)                     # final weights: all pairs
    table = learned_weight_table(mult, all_features, base)
    out_path = Path(out_path)
    out_path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    report = {
        "method": "one multiplier per feature shared across bands (bounds 0.25-4), loss = mean log(rank) "
                  f"+ {LAMBDA} * mean(log(mult)^2); {n_draws} random draws then Nelder-Mead; "
                  "leave-one-Mars-site-out evaluation",
        "multipliers": {f: round(float(m), 3) for f, m in zip(all_features, mult)},
        "n_pairs": len(items), "n_groups": len({it["group"] for it in items}),
        "loo": {"learned": learned, "hand_set": hand},
        "loo_pairs": rows,
        "adopted": bool(adopt),
        "verdict": ("Learned weights beat the hand-set weights in leave-one-site-out testing and are used by default."
                    if adopt else
                    "Learned weights did NOT beat the hand-set weights in leave-one-site-out testing; "
                    "hand-set weights stay the default (learned ones are available as an option)."),
    }
    return table, report


def load_tables():
    from pipeline.matching import load_tables as lt
    mars_long, earth_long = lt.read_long("mars"), lt.read_long("earth")
    mars, earth = {}, {}
    for band in evaluate.BANDS:
        mars[band], earth[band] = lt.load_band_tables(band, mars_long=mars_long, earth_long=earth_long)
    return mars, earth


if __name__ == "__main__":
    mars, earth = load_tables()
    table, report = run(mars, earth, evaluate.load_known_pairs())
    print(json.dumps({k: v for k, v in report.items() if k != "loo_pairs"}, indent=2))
