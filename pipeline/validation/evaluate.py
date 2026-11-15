"""Validation: does the scoring find known Mars-Earth analog pairs? (plan s11)

Idea: scientists already agree on some pairs, e.g. Phoenix landing site -> Dry Valleys.
For each known pair we ask the scoring "rank every Earth region for this Mars spot"
and check where the agreed Earth region lands.

- recall@K = share of pairs whose agreed Earth region is in the top K.
- random baseline = the same, but with regions shuffled at random (1,000 runs).
  If our recall is clearly higher than random, the scoring is finding real signal.
- mean_rank = average position of the agreed region (1 = top). Lower is better.
- ablation = switch off one feature at a time and see how much worse it gets,
  which shows which features actually matter.

All plain maths, using exactly the same code as the app's matching (match.py).

In:  Mars and Earth feature tables per band, pipeline/validation/known_pairs.csv.
Out: output/validation.json  {matcher, recall_at_10, recall_at_20,
     random_recall_at_20, per_band, n_pairs, notes, ...extra detail}
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.matching import match, normalize

HERE = Path(__file__).resolve().parent
PAIRS_PATH = HERE / "known_pairs.csv"
OUTPUT_PATH = HERE.parents[1] / "output" / "validation.json"
BANDS = ["skin", "upper_metre", "drill_zone", "deep"]
TOP_KS = [1, 3, 10, 20]          # which recall@K values to report


def load_known_pairs(path=PAIRS_PATH):
    """Read known_pairs.csv (columns: pair_id, mars_name, mars_lat, mars_lon,
    earth_region_id, depth_band, source_url)."""
    return pd.read_csv(path)


def nearest_mars_row(mars, lat, lon):
    """Row position of the Mars grid cell closest to (lat, lon).

    The haversine helper uses Earth's radius, but we only need "which is closest",
    and that answer is the same on any sphere.
    """
    gaps = match.distance_km(lat, lon, mars["lat"].to_numpy(float), mars["lon"].to_numpy(float))
    return int(np.argmin(gaps))


def region_ranking(distances, region_ids):
    """Rank Earth regions from closest to farthest.

    A region can have several cells; its distance is its BEST (smallest) cell.
    Regions with no comparable cells go last. Returns a list of region ids.
    """
    table = pd.DataFrame({"region": region_ids, "distance": distances})
    best = table.groupby("region")["distance"].min()
    return list(best.sort_values(na_position="last").index)


def rank_of(region, ranking):
    """Position (1 = top) of a region in a ranking, or None if it isn't there."""
    return ranking.index(region) + 1 if region in ranking else None


def summarize(ranks, n_regions, runs, rng):
    """Turn a list of ranks into recall@K, mean rank and the random baseline."""
    ranks = np.array(ranks, dtype=float)
    result = {"n_pairs": int(len(ranks))}
    if len(ranks) == 0:
        return result
    for k in TOP_KS:
        result[f"recall_at_{k}"] = round(float(np.mean(ranks <= k)), 3)
    result["mean_rank"] = round(float(ranks.mean()), 2)

    # Random baseline: give each pair's agreed region a random position, many times.
    random_ranks = rng.integers(1, n_regions + 1, size=(runs, len(ranks)))
    for k in TOP_KS:
        result[f"random_recall_at_{k}"] = round(float(np.mean(random_ranks <= k)), 3)
    result["random_mean_rank"] = round(float(random_ranks.mean()), 2)
    return result


def pair_ranks(prepared_by_band, pairs, notes):
    """For every usable known pair, the rank of its agreed Earth region.

    Returns a list of (band, rank). Pairs that can't be checked are skipped
    and explained in `notes`.
    """
    results = []
    for _, pair in pairs.iterrows():
        band = str(pair["depth_band"]).strip()
        if band not in prepared_by_band:
            notes.append(f"{pair['pair_id']}: no tables for band '{band}', skipped")
            continue
        prepared = prepared_by_band[band]
        region_ids = prepared["earth"]["region_id"].astype(str).to_numpy()
        if str(pair["earth_region_id"]) not in set(region_ids):
            notes.append(f"{pair['pair_id']}: Earth region '{pair['earth_region_id']}' "
                         f"not in the Earth library, skipped")
            continue
        m = nearest_mars_row(prepared["mars"], pair["mars_lat"], pair["mars_lon"])
        distances, _ = match.cell_distances(prepared, m)
        ranking = region_ranking(distances, region_ids)
        results.append((band, rank_of(str(pair["earth_region_id"]), ranking)))
    return results


def evaluate(mars_tables, earth_tables, pairs, matcher="knn", runs=1000, seed=0,
             feature_file=None, all_weights=None, ablation=True):
    """Run the whole validation.

    mars_tables, earth_tables: {band: DataFrame} feature tables.
    pairs: DataFrame from known_pairs.csv.
    Returns the dictionary written to output/validation.json.
    """
    feature_file = feature_file or normalize.load_feature_file()
    all_weights = all_weights or match.load_weights()
    rng = np.random.default_rng(seed)    # fixed seed -> same numbers every run
    notes = []

    def prepare_all(weights):
        return {band: match.prepare_band(mars_tables[band], earth_tables[band], band,
                                         feature_file, weights)
                for band in BANDS if band in mars_tables and band in earth_tables}

    prepared_by_band = prepare_all(all_weights)
    found = pair_ranks(prepared_by_band, pairs, notes)
    n_regions = max((p["earth"]["region_id"].nunique() for p in prepared_by_band.values()),
                    default=0)

    overall = summarize([rank for _, rank in found], n_regions, runs, rng)
    per_band = {}
    for band in BANDS:
        band_ranks = [rank for b, rank in found if b == band]
        if band_ranks:
            band_regions = prepared_by_band[band]["earth"]["region_id"].nunique()
            per_band[band] = summarize(band_ranks, band_regions, runs, rng)

    # Ablation: switch off one feature at a time (weight 0) and re-check mean rank.
    # A big jump in mean rank = that feature really helps find the right site.
    ablation_result = {}
    if ablation and found:
        for name in feature_file["features"]:
            weights_without = {band: {**w, name: 0.0} for band, w in all_weights.items()}
            ranks = [rank for _, rank in
                     pair_ranks(prepare_all(weights_without), pairs, notes=[])]
            ablation_result[name] = round(float(np.mean(ranks)) - overall["mean_rank"], 2)

    if n_regions and n_regions <= 20:
        notes.append(f"Only {n_regions} Earth regions: recall@10/20 are easy to reach, "
                     f"so recall_at_1/3 and mean_rank are the more telling numbers.")

    return {
        "matcher": matcher,
        "recall_at_10": overall.get("recall_at_10"),
        "recall_at_20": overall.get("recall_at_20"),
        "random_recall_at_20": overall.get("random_recall_at_20"),
        "per_band": per_band,
        "n_pairs": overall["n_pairs"],
        "notes": notes,
        # Extra detail (not required by the plan, useful for the pitch):
        "overall": overall,
        "n_regions": int(n_regions),
        "ablation_mean_rank_change": ablation_result,
        "fake": False,
    }


def write_validation(result, path=OUTPUT_PATH):
    """Save the validation result as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return path
