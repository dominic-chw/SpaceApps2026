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


SPACING_KM = 200   # same rule as the app's twins (config min_twin_spacing_km)


def spaced_ranks(distances, lats, lons, spacing_km=SPACING_KM):
    """Rank Earth places the way the app shows them: closest first, 200 km apart.

    A place within spacing_km of a better-ranked place shares that place's rank
    (the app would show the better one instead), so a cluster of near-identical
    neighbouring cells counts as ONE place. Returns (rank per place, number of distinct places).
    """
    order = np.argsort(np.where(np.isnan(distances), np.inf, distances))
    rank = np.zeros(len(distances), dtype=int)
    c_lat = np.empty(len(distances))
    c_lon = np.empty(len(distances))
    k = 0
    for i in order:
        if k:
            gaps = match.distance_km(lats[i], lons[i], c_lat[:k], c_lon[:k])
            j = int(np.argmin(gaps))
            if gaps[j] < spacing_km:
                rank[i] = j + 1
                continue
        c_lat[k], c_lon[k] = lats[i], lons[i]
        k += 1
        rank[i] = k
    return rank, k


def rank_of(region, ranking):
    """Position (1 = top) of a region in a ranking, or None if it isn't there."""
    return ranking.index(region) + 1 if region in ranking else None


def random_best_ranks(n_places, n_targets, runs, rng):
    """Random baseline for one pair: shuffle all places, take the best position of its targets.

    With 1 target this is a uniform position 1..n; with several (all cells near an Earth site)
    the best of them is naturally higher up, so the baseline stays fair.
    """
    m = max(1, min(int(n_targets), n_places))
    # best of m positions drawn without replacement from 1..n, done `runs` times
    positions = np.argsort(rng.random((runs, n_places)), axis=1)[:, :m] + 1 if n_places <= 5000 \
        else rng.integers(1, n_places + 1, size=(runs, m))
    return positions.min(axis=1)


def summarize(ranks, n_regions, runs, rng, n_targets=None):
    """Turn a list of ranks into recall@K, mean rank and the random baseline.

    n_regions: number of places ranked (one number, or one per pair)."""
    ranks = np.array(ranks, dtype=float)
    result = {"n_pairs": int(len(ranks))}
    if len(ranks) == 0:
        return result
    for k in TOP_KS:
        result[f"recall_at_{k}"] = round(float(np.mean(ranks <= k)), 3)
    result["mean_rank"] = round(float(ranks.mean()), 2)

    # Random baseline: give each pair's agreed site(s) random positions, many times.
    n_targets = n_targets or [1] * len(ranks)
    n_places = n_regions if np.ndim(n_regions) else [n_regions] * len(ranks)
    random_ranks = np.column_stack([random_best_ranks(int(n), m, runs, rng)
                                    for n, m in zip(n_places, n_targets)])
    for k in TOP_KS:
        result[f"random_recall_at_{k}"] = round(float(np.mean(random_ranks <= k)), 3)
    result["random_mean_rank"] = round(float(random_ranks.mean()), 2)
    return result


SITE_RADIUS_KM = 100   # an Earth cell this close to a pair's Earth site counts as "that site"


def pair_targets(pair, prepared):
    """Site ids that count as the pair's agreed Earth site.

    - earth_region_id given (e.g. "dry_valleys") -> that region.
    - else earth_lat/earth_lon given -> every Earth place within SITE_RADIUS_KM of the point
      (the global grid has no names for most sites, e.g. Tuktoyaktuk).
    """
    region = pair.get("earth_region_id")
    if isinstance(region, str) and region.strip():
        return {region.strip()} & set(prepared["site_ids"])
    lat, lon = pair.get("earth_lat"), pair.get("earth_lon")
    if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
        return set()
    gaps = match.distance_km(float(lat), float(lon), prepared["earth_lats"], prepared["earth_lons"])
    return set(np.asarray(prepared["site_ids"])[gaps <= SITE_RADIUS_KM])


def pair_ranks(prepared_by_band, pairs, notes):
    """For every usable known pair, the best rank of its agreed Earth site.

    Ranking uses the app's 200 km spacing (spaced_ranks), so the random baseline is
    "one random place among the distinct places". Returns a list of
    (band, rank, 1, number of distinct places). Pairs that can't be checked are skipped
    and explained in `notes`.
    """
    results = []
    for _, pair in pairs.iterrows():
        band = str(pair["depth_band"]).strip()
        if band not in prepared_by_band:
            notes.append(f"{pair['pair_id']}: no tables for band '{band}', skipped")
            continue
        prepared = prepared_by_band[band]
        targets = pair_targets(pair, prepared)
        if not targets:
            region = pair.get("earth_region_id")
            where = region if isinstance(region, str) and region else pair.get("earth_name", "its Earth site")
            notes.append(f"{pair['pair_id']}: {where} not in the Earth library "
                         f"(no cell within {SITE_RADIUS_KM} km), skipped")
            continue
        m = nearest_mars_row(prepared["mars"], pair["mars_lat"], pair["mars_lon"])
        distances, _ = match.cell_distances(prepared, m)
        ranks, n_places = spaced_ranks(distances, prepared["earth_lats"], prepared["earth_lons"])
        is_target = np.isin(np.asarray(prepared["site_ids"]), list(targets))
        results.append((band, int(ranks[is_target].min()), 1, n_places))
    return results


def evaluate(mars_tables, earth_tables, pairs, matcher="knn", runs=1000, seed=0,
             feature_file=None, all_weights=None, ablation=True, learned_report=None):
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
    n_regions = max((n for *_, n in found), default=0)    # distinct places (200 km apart)

    overall = summarize([r for _, r, _, _ in found], [n for *_, n in found], runs, rng,
                        [m for _, _, m, _ in found])
    per_band = {}
    for band in BANDS:
        band_found = [(r, m, n) for b, r, m, n in found if b == band]
        if band_found:
            per_band[band] = summarize([r for r, _, _ in band_found], [n for *_, n in band_found],
                                       runs, rng, [m for _, m, _ in band_found])

    # Ablation: switch off one feature at a time (weight 0) and re-check mean rank.
    # A big jump in mean rank = that feature really helps find the right site.
    ablation_result = {}
    if ablation and found:
        for name in feature_file["features"]:
            weights_without = {band: {**w, name: 0.0} for band, w in all_weights.items()}
            ranks = [r for _, r, _, _ in pair_ranks(prepare_all(weights_without), pairs, notes=[])]
            ablation_result[name] = round(float(np.mean(ranks)) - overall["mean_rank"], 2)

    if n_regions and n_regions <= 20:
        notes.append(f"Only {n_regions} Earth places: recall@10/20 are easy to reach, "
                     f"so recall_at_1/3 and mean_rank are the more telling numbers.")

    result = {
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
    if learned_report:
        # Extra key (plan s3 allows additions): learned weights vs hand-set vs random, all
        # leave-one-Mars-site-out (pipeline/validation/learn_weights.py).
        result["learned_weights"] = {k: v for k, v in learned_report.items() if k != "loo_pairs"}
    return result


def write_validation(result, path=OUTPUT_PATH):
    """Save the validation result as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return path
