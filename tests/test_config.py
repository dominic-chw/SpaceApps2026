from pipeline.common.config import get_processing
from pipeline.common.coords import snap


def test_defaults_and_override():
    assert get_processing()["grid_deg"] == 2
    assert get_processing({"grid_deg": 5})["grid_deg"] == 5


def test_snap_uses_params():
    assert snap(18.4, 77.5) == (19.0, 77.0)
    assert snap(18.4, 77.5, {"grid_deg": 10}) == (15.0, 75.0)
