"""Active-window box, porosity, diameters, and the Fluent integral count."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ro.active_window_geometry import (
    ACTIVE_WINDOW_CHANNEL_HEIGHT_M,
    active_window_box_volume_m3,
    active_window_porosity,
    active_window_x_condition,
    geometric_hydraulic_diameter_m,
    schock_miquel_hydraulic_diameter_m,
)
from ro.campaign_geometry import CAMPAIGN_H_M
from ro.fluent_report_helpers import (
    ACTIVE_WINDOW_MEMBRANE_AREA_REPORT,
    ACTIVE_WINDOW_MEMBRANE_CLIP,
    ACTIVE_WINDOW_SPACER_AREA_REPORT,
    ACTIVE_WINDOW_SPACER_CLIP,
    measure_active_window_geometry,
)
from helpers import REPO_ROOT

LENGTH = 7 * 0.003465
WIDTH = 0.003465
HEIGHT = CAMPAIGN_H_M


class _Range:
    def __init__(self):
        self.minimum = None
        self.maximum = None


class _Settings:
    def __init__(self):
        self.field = None
        self.surfaces = None
        self.range = _Range()
        self.report_type = None
        self.surface_names = None
        self.per_surface = None

    def set_state(self, state):
        for key, value in state.items():
            setattr(self, key, value)


class _Group:
    def __init__(self):
        self.objects = {}
        self.created = []
        self.deleted = []

    def get_object_names(self):
        return list(self.objects)

    def create(self, name):
        self.created.append(name)
        self.objects[name] = _Settings()
        return self.objects[name]

    def delete(self, name):
        self.deleted.append(name)
        self.objects.pop(name, None)

    def __getitem__(self, name):
        return self.objects[name]


class _Reduction:
    def __init__(self, volume):
        self.volume = volume
        self.calls = []

    def sum_if(self, *, expression, condition, weight, locations):
        self.calls.append(
            {
                "expression": expression,
                "condition": condition,
                "weight": weight,
                "locations": locations,
            }
        )
        return self.volume


def _session(areas):
    iso_clip = _Group()
    surface = _Group()
    calls = []

    def compute(*, report_defs):
        name = report_defs[0]
        calls.append(name)
        report = surface[name]
        assert report.report_type == "surface-area"
        assert report.surface_names
        return {name: areas[name]}

    solver = SimpleNamespace(
        settings=SimpleNamespace(
            results=SimpleNamespace(
                surfaces=SimpleNamespace(iso_clip=iso_clip)
            ),
            solution=SimpleNamespace(
                report_definitions=SimpleNamespace(
                    surface=surface,
                    compute=compute,
                )
            ),
        )
    )
    return solver, solver.settings.solution, iso_clip, calls


def test_box_porosity_and_both_diameters():
    box = active_window_box_volume_m3(LENGTH, WIDTH, HEIGHT)
    assert box == pytest.approx(LENGTH * WIDTH * HEIGHT)
    assert ACTIVE_WINDOW_CHANNEL_HEIGHT_M == HEIGHT
    fluid = 0.8 * box
    assert active_window_porosity(fluid, box) == pytest.approx(0.8)
    membrane = 0.9 * 2.0 * LENGTH * WIDTH
    spacer = 0.001
    geometric = geometric_hydraulic_diameter_m(fluid, membrane, spacer)
    schock = schock_miquel_hydraulic_diameter_m(0.8, HEIGHT, spacer, box)
    assert geometric == pytest.approx(4.0 * fluid / (membrane + spacer))
    assert schock == pytest.approx(4.0 * 0.8 / (2.0 / HEIGHT + spacer / box))
    assert geometric != pytest.approx(schock)


def test_empty_channel_both_diameters_are_two_heights():
    box = active_window_box_volume_m3(LENGTH, WIDTH, HEIGHT)
    membrane = 2.0 * LENGTH * WIDTH
    geometric = geometric_hydraulic_diameter_m(box, membrane, 0.0)
    schock = schock_miquel_hydraulic_diameter_m(1.0, HEIGHT, 0.0, box)
    assert geometric == pytest.approx(2.0 * HEIGHT)
    assert schock == pytest.approx(2.0 * HEIGHT)
    assert schock_miquel_hydraulic_diameter_m(1.0, HEIGHT, 0.001, box) is None
    assert geometric_hydraulic_diameter_m(box, 0.0, 0.0) is None


def test_measure_is_one_volume_integral_and_one_surface_integral_per_family():
    box = LENGTH * WIDTH * HEIGHT
    fluid = 0.8 * box
    reduction = _Reduction(fluid)
    areas = {
        ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: 0.002,
        ACTIVE_WINDOW_SPACER_AREA_REPORT: 0.001,
    }
    solver, solution, iso_clip, computes = _session(areas)
    locations = [object()]
    measured = measure_active_window_geometry(
        solver,
        solution,
        reduction,
        locations,
        ["wall_top_mem", "wall_bottom_mem"],
        ["wall_spacer_1", "wall_spacer_2"],
        0.003465,
        0.02772,
        LENGTH,
        WIDTH,
        HEIGHT,
    )
    assert reduction.calls == [
        {
            "expression": "1",
            "condition": active_window_x_condition(0.003465, 0.02772),
            "weight": "Volume",
            "locations": locations,
        }
    ]
    assert computes == [
        ACTIVE_WINDOW_MEMBRANE_AREA_REPORT,
        ACTIVE_WINDOW_SPACER_AREA_REPORT,
    ]
    assert iso_clip.created == [
        ACTIVE_WINDOW_MEMBRANE_CLIP,
        ACTIVE_WINDOW_SPACER_CLIP,
    ]
    assert iso_clip.deleted == iso_clip.created
    assert measured["fluent_surface_integrals"] == 2
    assert measured["fluent_volume_integrals"] == 1
    assert measured["active_window_fluid_volume_m3"] == pytest.approx(fluid)
    assert measured["active_window_membrane_area_m2"] == pytest.approx(0.002)
    assert measured["active_window_spacer_area_m2"] == pytest.approx(0.001)
    assert measured["active_window_box_volume_m3"] == pytest.approx(box)
    assert measured["active_window_porosity"] == pytest.approx(0.8)


def test_empty_channel_skips_the_spacer_surface_integral():
    box = LENGTH * WIDTH * HEIGHT
    reduction = _Reduction(box)
    solver, solution, iso_clip, computes = _session(
        {ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: 0.002}
    )
    measured = measure_active_window_geometry(
        solver,
        solution,
        reduction,
        [object()],
        ["wall_top_mem"],
        [],
        0.0,
        LENGTH,
        LENGTH,
        WIDTH,
        HEIGHT,
    )
    assert computes == [ACTIVE_WINDOW_MEMBRANE_AREA_REPORT]
    assert iso_clip.created == [ACTIVE_WINDOW_MEMBRANE_CLIP]
    assert measured["active_window_spacer_area_m2"] == 0.0
    assert measured["fluent_surface_integrals"] == 1
    assert measured["fluent_volume_integrals"] == 1
    assert len(reduction.calls) == 1


def test_a_failed_volume_integral_raises():
    reduction = _Reduction(0.0)
    solver, solution, _iso_clip, _computes = _session(
        {ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: 0.002}
    )
    with pytest.raises(RuntimeError, match="fluid volume"):
        measure_active_window_geometry(
            solver,
            solution,
            reduction,
            [object()],
            ["wall_top_mem"],
            [],
            0.0,
            LENGTH,
            LENGTH,
            WIDTH,
            HEIGHT,
        )


def test_extract_runs_the_phase_on_both_profiles():
    source = (
        REPO_ROOT / "scripts" / "pyfluent_report_extract.py"
    ).read_text(encoding="utf-8")
    start = source.index('_begin_extract_phase("active_window_geometry")')
    end = source.index('_begin_extract_phase("cell_8_25_salt_reduction")')
    block = source[start:end]
    assert "measure_active_window_geometry" in block
    assert "PROFILE_MFBO" not in block
    assert "CAMPAIGN_H_M" in block
    description = source[
        source.index('"active_window_geometry":'): source.index(
            '"cell_8_25_salt_reduction":'
        )
    ]
    assert "one surface-area integral" in description
    assert "one volume integral" in description
