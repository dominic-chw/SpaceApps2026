"""Coordinate checks, including the Jezero crater sanity check (18.4 N, 77.5 E)."""
from pipeline.common.coords import cell_id, grid_cells, in_range, snap, wrap_lon


def test_jezero_snaps_to_expected_cell():
    assert snap(18.4, 77.5) == (19, 77)           # 2 degree cell centre
    assert cell_id(18.4, 77.5) == "+019.0_+077.0"


def test_jezero_in_all_longitude_conventions():
    assert wrap_lon(77.5) == 77.5
    assert wrap_lon(77.5 + 360) == 77.5            # 0..360 east
    assert wrap_lon(282.5) == -77.5                # 0..360 east, western side
    assert wrap_lon(-77.5, west_positive=True) == 77.5
    assert snap(18.4, 77.5 + 360) == snap(18.4, 77.5)


def test_edges():
    assert wrap_lon(180) == -180
    assert snap(90, 0)[0] == 89                    # pole stays in the top cell
    assert snap(-90, 0)[0] == -89


def test_grid_contains_jezero_cell_and_respects_limit():
    cells = grid_cells()
    assert (19, 77) in cells
    assert all(in_range(la) for la, _ in cells)
    assert not in_range(71)
    assert len(cells) == 70 * 180                  # lat centres -69..69
