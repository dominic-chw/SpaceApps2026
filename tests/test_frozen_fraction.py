"""frozen_fraction: share of the year below 0 C at a band's depth (heat model + tables)."""
import math

import pandas as pd

from pipeline.common.config import get_processing
from pipeline.common.schema import BANDS
from pipeline.earth_library.build_grid import build_earth
from pipeline.mars_features.grid import build_mars
from pipeline.physics.heat_model import earth_frozen_fraction, mars_frozen_fraction


def test_limits():
    assert mars_frozen_fraction(0, 250, "drill_zone") == 1.0            # Mars: frozen all year
    assert earth_frozen_fraction(295, 20, 1500, "upper_metre") == 0.0   # hot desert
    assert earth_frozen_fraction(250, 20, 1500, "upper_metre") == 1.0   # polar
    assert math.isclose(earth_frozen_fraction(273.15, 20, 1500, "skin"), 0.5, abs_tol=1e-6)


def test_deeper_is_steadier():
    # Mean just below 0 C: the surface thaws part of the year, deeper stays frozen.
    skin = earth_frozen_fraction(271, 20, 1500, "skin")
    deep = earth_frozen_fraction(271, 20, 1500, "deep")
    assert skin < deep <= 1.0


def test_in_both_tables(tmp_path):
    p = get_processing({"grid_deg": 30})
    for path, name in ((build_mars(p, tmp_path), "mars"), (build_earth(p, tmp_path), "earth")):
        df = pd.read_parquet(path)
        ff = df[df.feature == "frozen_fraction"]
        assert set(ff.band) == set(BANDS) and ff.value_num.between(0, 1).all(), name
        assert ff.data_quality.notna().all()
    earth_temp = pd.read_parquet(tmp_path / "earth_cells.parquet")
    t = earth_temp[earth_temp.feature == "temp_mean"].value_num
    assert t.between(200, 320).all()        # Earth ground is not Mars-cold
