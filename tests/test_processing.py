import math

from pipeline.common.config import get_processing
from pipeline.common.coords import cell_id, grid_cells
from pipeline.common.schema import BANDS, GROUND, QUALITY, ROCK
from pipeline.common import io
from pipeline.earth_library.build_grid import build_earth
from pipeline.mars_features.grid import build_mars
from pipeline.physics.heat_model import mars_band_temps


def test_jezero_cell():
    assert cell_id(18.4, 77.5) == "+019.0_+077.0"


def test_grid_size_follows_config():
    assert len(grid_cells({"grid_deg": 2, "lat_limit": 70})) == 70 * 180
    assert len(grid_cells({"grid_deg": 10, "lat_limit": 70})) == 14 * 36


def test_swing_decays_with_depth():
    s = [mars_band_temps(40, 250, b)[1] for b in BANDS]
    assert s == sorted(s, reverse=True) and s[0] > 20


def test_tables_match_contract(tmp_path):
    p = get_processing({"grid_deg": 20})
    for planet, df in (("mars", io.read_features("mars", build_mars(p, tmp_path).parent)),
                       ("earth", io.read_features("earth", build_earth(p, tmp_path).parent))):
        assert set(df.band) == set(BANDS) and set(df.data_quality) <= set(QUALITY)
        assert set(df[df.feature == "rock"].value_cat) <= set(ROCK)
        assert set(df[df.feature == "ground_class"].value_cat) <= set(GROUND)
        h = df[df.feature == "hardness"].value_num
        assert h.between(0, 3).all()
        assert not df[df.feature == "temp_swing"].band.eq("deep").any()
        assert not df[df.feature == "dryness"].band.isin(["drill_zone", "deep"]).any()
        assert df.value_num[df.feature == "temp_mean"].map(math.isfinite).all()
