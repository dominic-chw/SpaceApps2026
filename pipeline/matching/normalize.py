"""Step 1 of scoring: put every feature on a common scale so Mars and Earth can be compared.

Plan s10: climate as within-planet percentiles, terrain log + z-score (Earth stats),
categories compared as same/different.

Which features exist, and how each is compared, comes from features.yaml.
Nothing here is hard-coded to a feature name, so Markus can change the list freely.

In:  a Mars table and an Earth table for ONE depth band (pandas DataFrames,
     one row per place, one column per raw feature).
Out: two number grids (numpy arrays) with the same columns, ready for match.py,
     plus the settings used (saved later to earth_library.json for the web sliders).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# Where the feature list lives (next to this file).
FEATURES_PATH = Path(__file__).with_name("features.yaml")

# Words in an "ice" style yes/no column that all mean the same thing.
YES_WORDS = {"yes", "true", "1", "1.0", "y"}
NO_WORDS = {"no", "false", "0", "0.0", "n"}


def load_feature_file(path=FEATURES_PATH):
    """Read features.yaml and return it as a Python dictionary."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def features_for_band(feature_file, band):
    """Return only the features that apply to this depth band.

    Result: {feature_name: settings} in the same order as features.yaml.
    """
    chosen = {}
    for name, settings in feature_file["features"].items():
        bands = settings.get("bands", "all")
        if bands == "all" or band in bands:
            chosen[name] = settings
    return chosen


def clean_word(value):
    """Tidy one category value: lower case, no spaces, yes/no spelled one way.

    Missing values stay missing (NaN), so they can be skipped later.
    """
    if pd.isna(value):
        return np.nan
    word = str(value).strip().lower()
    if word in YES_WORDS:
        return "yes"
    if word in NO_WORDS:
        return "no"
    return word


def normalize_pair(mars, earth, features):
    """Turn raw Mars and Earth tables into comparable number grids.

    mars, earth: DataFrames for one band. Must contain each feature's `column`
                 (a missing column is treated as all-missing, not an error).
    features:    output of features_for_band().

    Returns (mars_grid, earth_grid, settings):
      mars_grid:  numpy array, one row per Mars place, one column per feature.
      earth_grid: same for Earth.
      settings:   what was used to normalize (e.g. Earth averages), per feature.
    Missing values are NaN in the grids.
    """
    mars_columns = []
    earth_columns = []
    settings = {}

    for name, feature in features.items():
        column = feature["column"]
        compare = feature["compare"]

        # Get the raw values; a column that does not exist yet = all missing.
        mars_raw = mars[column] if column in mars else pd.Series(np.nan, index=mars.index)
        earth_raw = earth[column] if column in earth else pd.Series(np.nan, index=earth.index)

        if compare == "percentile":
            # Rank each place within its OWN planet: 0 = lowest, 1 = highest.
            # Example: the coldest Mars cell and the coldest Earth site both get ~0,
            # even though Mars is about 80 K colder.
            mars_values = pd.to_numeric(mars_raw, errors="coerce").rank(pct=True)
            earth_values = pd.to_numeric(earth_raw, errors="coerce").rank(pct=True)
            settings[name] = {"compare": compare}

        elif compare == "absolute":
            # Keep the real values (e.g. kelvin); match.py divides by `scale`.
            mars_values = pd.to_numeric(mars_raw, errors="coerce")
            earth_values = pd.to_numeric(earth_raw, errors="coerce")
            settings[name] = {"compare": compare, "scale": feature["scale"]}

        elif compare == "log_z":
            # Terrain values are skewed (most places flat, a few very steep),
            # so take the log first, then measure "how many standard deviations
            # from Earth's average". Both planets use EARTH's average and spread,
            # so the numbers stay comparable.
            mars_log = np.log1p(pd.to_numeric(mars_raw, errors="coerce").clip(lower=0))
            earth_log = np.log1p(pd.to_numeric(earth_raw, errors="coerce").clip(lower=0))
            average = earth_log.mean()
            spread = earth_log.std()
            if not spread or np.isnan(spread):
                spread = 1.0  # all Earth values equal (or none): avoid dividing by 0
            if np.isnan(average):
                average = 0.0
            mars_values = (mars_log - average) / spread
            earth_values = (earth_log - average) / spread
            settings[name] = {"compare": compare, "scale": feature["scale"],
                              "earth_log_mean": float(average), "earth_log_std": float(spread)}

        elif compare == "same":
            # Words (rock type, ground type, ice yes/no). Give each word a number
            # code, using ONE shared list for both planets, so "volcanic" is the
            # same code on Mars and on Earth. Only equal/not-equal matters later.
            mars_words = mars_raw.map(clean_word)
            earth_words = earth_raw.map(clean_word)
            vocabulary = sorted(set(mars_words.dropna()) | set(earth_words.dropna()))
            allowed = feature.get("allowed")
            if allowed:
                unknown = set(vocabulary) - set(allowed)
                if unknown:
                    raise ValueError(
                        f"Feature '{name}' has words outside the fixed list {allowed}: "
                        f"{sorted(unknown)}. Translate them with a lookup table first."
                    )
            code_of = {word: i for i, word in enumerate(vocabulary)}
            mars_values = mars_words.map(code_of).astype(float)
            earth_values = earth_words.map(code_of).astype(float)
            settings[name] = {"compare": compare, "codes": code_of}

        elif compare == "steps":
            # Ordered scale such as hardness 0-3; match.py divides by (max - min).
            mars_values = pd.to_numeric(mars_raw, errors="coerce")
            earth_values = pd.to_numeric(earth_raw, errors="coerce")
            settings[name] = {"compare": compare, "min": feature["min"], "max": feature["max"]}

        else:
            raise ValueError(f"Feature '{name}': unknown compare method '{compare}'")

        mars_columns.append(np.asarray(mars_values, dtype=float))
        earth_columns.append(np.asarray(earth_values, dtype=float))

    # Stack the columns side by side into grids (rows = places, columns = features).
    mars_grid = np.column_stack(mars_columns) if mars_columns else np.empty((len(mars), 0))
    earth_grid = np.column_stack(earth_columns) if earth_columns else np.empty((len(earth), 0))
    return mars_grid, earth_grid, settings


def difference_scales(features):
    """For each feature, the number to divide a raw difference by so 1 = full mismatch.

    Returns two arrays: the scale per feature, and True/False "is this a same/different
    category" per feature.
    """
    scales = []
    is_category = []
    for feature in features.values():
        compare = feature["compare"]
        if compare == "percentile":
            scales.append(1.0)  # ranks already run from 0 to 1
        elif compare in ("absolute", "log_z"):
            scales.append(float(feature["scale"]))
        elif compare == "steps":
            scales.append(float(feature["max"] - feature["min"]))
        else:  # "same"
            scales.append(1.0)
        is_category.append(compare == "same")
    return np.array(scales), np.array(is_category)
