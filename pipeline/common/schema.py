"""Fixed vocabularies (plan s3) and the feature-table schema handed to matching.

Table: data/features/{mars,earth}_cells.parquet, long format, columns:
planet, cell_id, lat, lon, band, feature, value_num, value_cat, data_quality.
Numeric features go in value_num; category features (ground_class, rock) in
value_cat; data_quality is measured, modelled or predicted. Earth cells use the
region_id (or sample id) as cell_id.
"""
BANDS = ["skin", "upper_metre", "drill_zone", "deep"]
ROCK = ["volcanic", "sedimentary", "clay_sulfate", "icy", "impact_mixed"]
GROUND = ["dry_regolith", "ice_cemented", "massive_ice", "bedrock"]
QUALITY = ["measured", "modelled", "predicted"]
CATEGORICAL = {"ground_class", "rock"}
FEATURES = ["temp_mean", "temp_swing", "ice", "ground_class", "hardness", "rock",
            "slope_deg", "roughness_m", "dryness"]
# Bands each feature exists in (plan s6); others exist in all bands.
FEATURE_BANDS = {"temp_swing": BANDS[:3], "dryness": BANDS[:2]}
COLUMNS = ["planet", "cell_id", "lat", "lon", "band", "feature",
           "value_num", "value_cat", "data_quality"]


def feature_in_band(feature, band):
    return band in FEATURE_BANDS.get(feature, BANDS)
