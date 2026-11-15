"""Pluggable source loaders (pipeline/common/sources.py) on tiny fake arrays and tables. No downloads."""
import numpy as np
import pandas as pd
import pytest

from pipeline.common import sources
from pipeline.common.config import get_processing
from pipeline.common.synthetic import mars_layers


def cells_df(points):
    return pd.DataFrame({"cell_id": [f"c{i}" for i in range(len(points))],
                         "lat": [p[0] for p in points], "lon": [p[1] for p in points]})


def p_with(sources_cfg, **extra):
    return get_processing({"sources": sources_cfg, **extra})


def test_default_config_is_all_synthetic_and_assemble_returns_none():
    p = get_processing()
    for planet in sources.VARIABLES:
        assert sources.all_synthetic(planet, p)
        assert sources.assemble(planet, p) is None


def test_unknown_variable_rejected():
    with pytest.raises(ValueError):
        sources.source_specs("earth", p_with({"earth": {"not_a_variable": {"source": "synthetic"}}}))


def test_grid_reduce_mean_median_mode_and_nodata():
    # 2 x 4 raster, 1 deg pixels, west -2, north 1: row 0 covers lat 0..1, row 1 lat -1..0
    a = np.array([[1, 2, 3, -9], [5, 6, 7, 7]], float)
    cells = cells_df([(0.0, -1.0), (0.0, 1.0)])   # boxes +-1 deg: cols 0-1 and cols 2-3
    assert np.allclose(sources.grid_reduce(a, -2, 1, 1, 1, cells, 1.0, "mean"), [3.5, (3 - 9 + 7 + 7) / 4])
    assert np.allclose(sources.grid_reduce(a, -2, 1, 1, 1, cells, 1.0, "mean", nodata=-9), [3.5, 17 / 3])
    assert np.allclose(sources.grid_reduce(a, -2, 1, 1, 1, cells, 1.0, "median", nodata=-9), [3.5, 7])
    assert np.allclose(sources.grid_reduce(a, -2, 1, 1, 1, cells, 1.0, "mode", nodata=-9), [1, 7])


def test_grid_reduce_outside_raster_is_nan():
    v = sources.grid_reduce(np.ones((2, 2)), 0, 2, 1, 1, cells_df([(50.0, 50.0)]), 1.0)
    assert np.isnan(v).all()


def test_nearest_join_handles_dateline():
    table = pd.DataFrame({"lat": [0.0, 0.0], "lon": [179.5, 0.0], "x": [1.0, 2.0]})
    out = sources.nearest_join(cells_df([(0.0, -179.5), (1.0, 1.0)]), table, ["x"])
    assert list(out["x"]) == [1.0, 2.0]


def test_file_loader_by_cell_id_with_rename(tmp_path):
    f = tmp_path / "ti.csv"
    pd.DataFrame({"cell_id": ["c1", "c0"], "TI": [300.0, 100.0]}).to_csv(f, index=False)
    out = sources.load_file({"path": str(f), "columns": {"TI": "thermal_inertia"}}, ["thermal_inertia"],
                            cells_df([(0, 0), (2, 2), (4, 4)]), "mars", get_processing())
    assert list(out["thermal_inertia"].fillna(-1)) == [100.0, 300.0, -1]


def test_file_loader_nearest_with_lon_360(tmp_path):
    f = tmp_path / "w.csv"
    pd.DataFrame({"lat": [10.0, -10.0], "lon": [350.0, 10.0], "water_vapour_pr_um": [5.0, 9.0]}).to_csv(f, index=False)
    out = sources.load_file({"path": str(f), "lon_360": True, "match": "nearest"}, ["water_vapour_pr_um"],
                            cells_df([(9.0, -11.0), (-9.0, 11.0)]), "mars", get_processing())
    assert list(out["water_vapour_pr_um"]) == [5.0, 9.0]


def test_file_loader_missing_column_is_clear_error(tmp_path):
    f = tmp_path / "x.csv"
    pd.DataFrame({"cell_id": ["c0"], "other": [1]}).to_csv(f, index=False)
    with pytest.raises(ValueError, match="lacks columns"):
        sources.load_file({"path": str(f)}, ["slope_deg"], cells_df([(0, 0)]), "mars", get_processing())


def test_mixed_config_one_real_variable_rest_synthetic(tmp_path):
    p = get_processing({"grid_deg": 30})
    syn = mars_layers(p)
    f = tmp_path / "ti.parquet"
    syn[["cell_id"]].assign(thermal_inertia=123.0).to_parquet(f)
    p = p_with({"mars": {"thermal_inertia": {"source": "file", "path": str(f)}}}, grid_deg=30)
    layers = sources.assemble("mars", p)
    assert layers is not None and len(layers) == len(syn)
    assert (layers["thermal_inertia"] == 123.0).all()
    # every other column comes from the synthetic layers, aligned by cell_id
    merged = layers.merge(syn, on="cell_id", suffixes=("", "_syn"))
    assert np.allclose(merged["slope_deg"], merged["slope_deg_syn"])
    assert set(sources.VARIABLES["mars"]["ice"]) <= set(layers.columns)


def test_earth_grid_cells_include_regions_with_region_id():
    p = p_with({"earth": {"cells": {"source": "grid", "deg": 30, "lat_limit": 60}}}, earth_grid_deg=30)
    cells = sources._cells("earth", sources.source_specs("earth", p)["cells"], p)
    assert {"dry_valleys", "atacama_high"} <= set(cells["cell_id"])          # known analogs keep region_id
    named = cells[cells["name"] != ""]
    assert len(named) == 20 and cells["cell_id"].is_unique
    assert len(cells) == 20 + 4 * 12                                          # regions + 30 deg grid in +-60


def test_unknown_source_rejected():
    p = p_with({"mars": {"terrain": {"source": "magic"}}}, grid_deg=30)
    with pytest.raises(ValueError, match="unknown source"):
        sources.assemble("mars", p)


def test_export_ee_dry_run_slice_needs_ee_source():
    p = p_with({"earth": {"aridity": {"source": "ee", "id": "x", "bands": ["b1"], "collection": False,
                                      "path": "data/earth/ee/aridity.csv"}}})
    cells = sources.export_ee("aridity", params=p, region_ids=["atacama_high"], dry_run=True)
    assert list(cells["cell_id"]) == ["atacama_high"]
    with pytest.raises(ValueError):
        sources.export_ee("lithology", params=p, dry_run=True)
