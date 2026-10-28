"""Polyhedra bulk size stays off the production poly-hexcore payload."""

from __future__ import annotations

import pytest

from ro.bulk_size import (
    POLYHEDRA_GROWTH_RATE,
    bulk_max_size_suffix,
    volume_fill_arguments,
    volume_fill_summary_lines,
)


def test_unset_volume_fill_is_the_poly_hexcore_payload():
    payload = volume_fill_arguments(
        None,
        vol_hex_max=0.0595,
        peel_layers=2,
        growth_rate=POLYHEDRA_GROWTH_RATE,
    )
    assert payload == {
        "VolumeFill": "poly-hexcore",
        "VolumeFillControls": {
            "HexMaxCellLength": 0.0595,
            "PeelLayers": 2,
        },
    }
    assert volume_fill_summary_lines(
        bulk_max_size_mm=None,
        vol_hex_max_factor=0.7,
        vol_hex_max=0.0595,
        peel_layers=2,
        growth_rate=POLYHEDRA_GROWTH_RATE,
    ) == [
        "Volume mesh fill type: poly-hexcore",
        "Volume hex max factor [-]: 0.7",
        "Volume hex max cell length [mm]: 0.0595",
        "Peel layers [-]: 2",
    ]
    assert bulk_max_size_suffix(None) == ""


def test_bulk_size_fills_polyhedra_at_growth_1_2():
    payload = volume_fill_arguments(
        0.034,
        vol_hex_max=0.0245,
        peel_layers=2,
        growth_rate=POLYHEDRA_GROWTH_RATE,
    )
    assert payload == {
        "VolumeFill": "polyhedra",
        "VolumeFillControls": {
            "GrowthRate": 1.2,
            "TetPolyMaxCellLength": 0.034,
        },
    }
    lines = volume_fill_summary_lines(
        bulk_max_size_mm=0.034,
        vol_hex_max_factor=0.7,
        vol_hex_max=0.0245,
        peel_layers=2,
        growth_rate=POLYHEDRA_GROWTH_RATE,
    )
    assert lines[0] == "Volume mesh fill type: polyhedra"
    assert "Volume poly growth rate [-]: 1.2" in lines
    assert "Bulk max size [mm]: 0.034" in lines
    assert lines[-1] == "Peel layers [-]: 2"
    assert bulk_max_size_suffix(0.034) == "_bms034"


@pytest.mark.parametrize("size_mm", [0.0, -0.01, 1.0, 0.0004])
def test_bulk_token_rejects_sizes_outside_bmsnnn(size_mm):
    with pytest.raises(ValueError, match="_bmsNNN|positive"):
        bulk_max_size_suffix(size_mm)
