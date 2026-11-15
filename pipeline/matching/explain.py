"""Step 3 of scoring: explain each match and say how much to trust it.

- best[3]:  the 3 features that match most closely ("matched on").
- worst[3]: the 3 features that add the most to the distance ("differs on").
- confidence: high / medium / low, lowered for missing, modelled, predicted
  or deep values. Trust factors live in features.yaml (confidence section).

In:  per-feature differences for one Mars place vs one Earth place (from match.py).
Out: lists of feature names and a confidence label.
"""

import numpy as np
import pandas as pd

# A feature only counts as "matched on" if its difference is below this (0-1 scale).
CLOSE_ENOUGH = 0.5


def best_and_worst(differences, weights, names, how_many=3):
    """Pick the closest-matching and the most-different features.

    differences: array, one 0-1 difference per feature (NaN = missing, skipped).
    weights:     array, how much each feature counts in this band.
    names:       list of feature names, same order.

    best:  smallest differences first (ties: the more important feature first);
           only features closer than CLOSE_ENOUGH, so a mismatch is never "matched on".
    worst: biggest weight x difference first; perfect matches (0) are never "worst".
    """
    present = [i for i in range(len(names)) if not np.isnan(differences[i]) and weights[i] > 0]

    close = [i for i in present if differences[i] < CLOSE_ENOUGH]
    best = sorted(close, key=lambda i: (differences[i], -weights[i]))[:how_many]

    differing = [i for i in present if differences[i] > 0]
    worst = sorted(differing, key=lambda i: -(weights[i] * differences[i]))[:how_many]

    return [names[i] for i in best], [names[i] for i in worst]


def quality_grid(table, features, confidence_settings):
    """Turn the "<column>_quality" tags into trust factors (1 = fully trusted).

    Returns a numpy array: one row per place, one column per feature.
    A missing tag (or no quality column at all) uses `missing_tag`.
    """
    factor_of = confidence_settings["quality_factor"]
    default = factor_of["missing_tag"]
    columns = []
    for feature in features.values():
        tag_column = feature["column"] + "_quality"
        if tag_column in table:
            tags = table[tag_column].map(lambda t: str(t).strip().lower() if pd.notna(t) else None)
            columns.append(tags.map(lambda t: factor_of.get(t, default)).astype(float).to_numpy())
        else:
            columns.append(np.full(len(table), default))
    return np.column_stack(columns) if columns else np.empty((len(table), 0))


def confidence(differences, weights, mars_quality, earth_quality, band, confidence_settings):
    """Score how much to trust one match, then turn the score into a label.

    score = coverage x quality x band factor, where
      coverage = share of the total weight whose values were present on both planets
      quality  = weighted average trust of the present values (the weaker planet counts)
      band     = lower for deeper bands (less direct data)

    Returns (label, score), e.g. ("medium", 0.62).
    """
    present = ~np.isnan(differences) & (weights > 0)
    total_weight = weights[weights > 0].sum()
    if total_weight == 0 or not present.any():
        return "low", 0.0

    coverage = weights[present].sum() / total_weight
    trust = np.minimum(mars_quality, earth_quality)[present]
    quality = (weights[present] * trust).sum() / weights[present].sum()
    band_factor = confidence_settings["band_factor"].get(band, 1.0)
    score = coverage * quality * band_factor

    labels = confidence_settings["labels"]
    if score >= labels["high"]:
        label = "high"
    elif score >= labels["medium"]:
        label = "medium"
    else:
        label = "low"
    return label, round(float(score), 3)
