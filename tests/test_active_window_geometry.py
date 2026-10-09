"""Active-window box, porosity, diameters, and the Fluent integral count."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ro.active_window_geometry import (
    ACTIVE_WINDOW_CHANNEL_HEIGHT_M,
    active_window_box_volume_m3,
    active_window_porosity,
    buffer_fluid_volume_m3,
    geometric_hydraulic_diameter_m,
    require_active_window_geometry,
    schock_miquel_hydraulic_diameter_m,
)
from ro.campaign_geometry import CAMPAIGN_H_M
from ro.domain_layout import BUFFER_LENGTH_IN_M, BUFFER_LENGTH_OUT_M
from ro.fluent_report_helpers import (
    ACTIVE_WINDOW_MEMBRANE_AREA_REPORT,
    ACTIVE_WINDOW_MEMBRANE_CLIP,
    ACTIVE_WINDOW_SPACER_AREA_REPORT,
    ACTIVE_WINDOW_SPACER_CLIP,
    ACTIVE_WINDOW_VOLUME_REPORT,
    ACTIVE_WINDOW_VOLUME_REPORT_TYPE,
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


def _session(areas, fluid_volume, zone_volumes=None):
    iso_clip = _Group()
    surface = _Group()
    volume = _Group()
    registers = _Group()
    calls = []

    def compute(*, report_defs):
        name = report_defs[0]
        calls.append(name)
        if name == ACTIVE_WINDOW_VOLUME_REPORT or name.startswith(
            f"{ACTIVE_WINDOW_VOLUME_REPORT}_"
        ):
            report = volume[name]
            assert report.report_type == ACTIVE_WINDOW_VOLUME_REPORT_TYPE
            assert isinstance(report.cell_zones, str)
            assert report.field is None
            if zone_volumes is not None:
                return {name: zone_volumes[report.cell_zones]}
            assert report.cell_zones == "solid"
            return {name: fluid_volume}
        report = surface[name]
        assert report.report_type == "surface-area"
        assert report.surface_names
        return {name: areas[name]}

    solution = SimpleNamespace(
        cell_registers=registers,
        report_definitions=SimpleNamespace(
            surface=surface,
            volume=volume,
            compute=compute,
        ),
    )
    solver = SimpleNamespace(
        settings=SimpleNamespace(
            results=SimpleNamespace(
                surfaces=SimpleNamespace(iso_clip=iso_clip)
            ),
            solution=solution,
        )
    )
    return solver, solution, iso_clip, registers, calls


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


MEMBRANE_AREA = 1.0e-4


def _measure(solver, solution, *, spacer_zones, family, x_min=0.003465, x_max=0.02772):
    return measure_active_window_geometry(
        solver,
        solution,
        ["wall_top_mem", "wall_bottom_mem"],
        spacer_zones,
        ["solid"],
        x_min,
        x_max,
        LENGTH,
        BUFFER_LENGTH_IN_M,
        BUFFER_LENGTH_OUT_M,
        WIDTH,
        HEIGHT,
        family,
    )


def test_measure_is_one_volume_report_and_one_surface_integral_per_family():
    box = LENGTH * WIDTH * HEIGHT
    fluid = 0.8 * box
    buffers = buffer_fluid_volume_m3(BUFFER_LENGTH_IN_M, BUFFER_LENGTH_OUT_M, WIDTH, HEIGHT)
    areas = {
        ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: MEMBRANE_AREA,
        ACTIVE_WINDOW_SPACER_AREA_REPORT: 0.001,
    }
    solver, solution, iso_clip, registers, computes = _session(areas, fluid + buffers)
    measured = _measure(
        solver,
        solution,
        spacer_zones=["wall_spacer_1", "wall_spacer_2"],
        family="diamond",
    )
    assert computes == [
        ACTIVE_WINDOW_MEMBRANE_AREA_REPORT,
        ACTIVE_WINDOW_SPACER_AREA_REPORT,
        ACTIVE_WINDOW_VOLUME_REPORT,
    ]
    assert "sum_if" not in repr(computes)
    assert registers.created == []
    assert registers.deleted == []
    assert iso_clip.created == [
        ACTIVE_WINDOW_MEMBRANE_CLIP,
        ACTIVE_WINDOW_SPACER_CLIP,
    ]
    assert iso_clip.deleted == iso_clip.created
    assert measured["fluent_surface_integrals"] == 2
    assert measured["fluent_volume_integrals"] == 1
    assert measured["active_window_fluid_volume_m3"] == pytest.approx(fluid)
    assert measured["active_window_membrane_area_m2"] == pytest.approx(MEMBRANE_AREA)
    assert measured["active_window_spacer_area_m2"] == pytest.approx(0.001)
    assert measured["active_window_box_volume_m3"] == pytest.approx(box)
    assert measured["active_window_porosity"] == pytest.approx(0.8)


def test_two_fluid_zones_are_summed_then_the_buffer_boxes_are_removed():
    box = LENGTH * WIDTH * HEIGHT
    buffers = buffer_fluid_volume_m3(
        BUFFER_LENGTH_IN_M, BUFFER_LENGTH_OUT_M, WIDTH, HEIGHT
    )
    fluid = 0.8 * box
    zone_total = fluid + buffers
    areas = {
        ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: MEMBRANE_AREA,
        ACTIVE_WINDOW_SPACER_AREA_REPORT: 0.001,
    }
    solver, solution, _iso_clip, _registers, computes = _session(
        areas,
        None,
        zone_volumes={"solid": 0.4 * zone_total, "fluid-2": 0.6 * zone_total},
    )
    measured = measure_active_window_geometry(
        solver,
        solution,
        ["wall_top_mem", "wall_bottom_mem"],
        ["wall_spacer_1"],
        ["solid", "fluid-2"],
        0.003465,
        0.02772,
        LENGTH,
        BUFFER_LENGTH_IN_M,
        BUFFER_LENGTH_OUT_M,
        WIDTH,
        HEIGHT,
        "pillar",
    )
    volume = solution.report_definitions.volume
    assert volume[f"{ACTIVE_WINDOW_VOLUME_REPORT}_0"].cell_zones == "solid"
    assert volume[f"{ACTIVE_WINDOW_VOLUME_REPORT}_1"].cell_zones == "fluid-2"
    assert computes[-2:] == [
        f"{ACTIVE_WINDOW_VOLUME_REPORT}_0",
        f"{ACTIVE_WINDOW_VOLUME_REPORT}_1",
    ]
    assert measured["fluent_volume_integrals"] == 2
    assert measured["active_window_fluid_volume_m3"] == pytest.approx(fluid)
    assert buffers == pytest.approx(
        (BUFFER_LENGTH_IN_M + BUFFER_LENGTH_OUT_M) * WIDTH * HEIGHT
    )


def test_empty_channel_skips_the_spacer_surface_integral():
    box = LENGTH * WIDTH * HEIGHT
    fluid = 0.5 * box
    buffers = buffer_fluid_volume_m3(BUFFER_LENGTH_IN_M, BUFFER_LENGTH_OUT_M, WIDTH, HEIGHT)
    solver, solution, iso_clip, _registers, computes = _session(
        {ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: MEMBRANE_AREA},
        fluid + buffers,
    )
    measured = _measure(
        solver,
        solution,
        spacer_zones=[],
        family="empty",
        x_min=0.0,
        x_max=LENGTH,
    )
    assert computes == [
        ACTIVE_WINDOW_MEMBRANE_AREA_REPORT,
        ACTIVE_WINDOW_VOLUME_REPORT,
    ]
    assert iso_clip.created == [ACTIVE_WINDOW_MEMBRANE_CLIP]
    assert measured["active_window_fluid_volume_m3"] == pytest.approx(fluid)
    assert measured["active_window_spacer_area_m2"] == 0.0
    assert measured["fluent_surface_integrals"] == 1
    assert measured["fluent_volume_integrals"] == 1


def test_sum_if_face_count_fails_the_volume_guard():
    """PyFluent 0.38 sum_if ignores the weight and returns a face count.

    docs/RESTRUCTURE_PLAN.md: expression=\"1\" with weight=\"Area\" returned
    2213 where the iso-clip area is 1.126e-5 m^2. weight=\"Volume\" is not
    a separate, proven scale. That count must not be stored as a volume.
    """
    reduction = _Reduction(2213)
    counted = reduction.sum_if(
        expression="1",
        condition="x-coordinate >= 0.0 && x-coordinate <= 1.0",
        weight="Volume",
        locations=["fluid"],
    )
    assert counted == 2213
    assert reduction.calls[0]["weight"] == "Volume"
    box = active_window_box_volume_m3(LENGTH, WIDTH, HEIGHT)
    with pytest.raises(RuntimeError, match="fluid volume"):
        require_active_window_geometry(
            fluid_volume_m3=counted,
            membrane_area_m2=MEMBRANE_AREA,
            spacer_area_m2=0.001,
            box_volume_m3=box,
            active_length_m=LENGTH,
            periodic_shift_y_m=WIDTH,
            family="diamond",
        )


def test_volume_report_that_returns_a_face_count_aborts():
    solver, solution, _iso_clip, registers, _computes = _session(
        {
            ACTIVE_WINDOW_MEMBRANE_AREA_REPORT: MEMBRANE_AREA,
            ACTIVE_WINDOW_SPACER_AREA_REPORT: 0.001,
        },
        2213,
    )
    with pytest.raises(RuntimeError, match="fluid volume"):
        _measure(
            solver,
            solution,
            spacer_zones=["wall_spacer_1"],
            family="pillar",
        )
    assert registers.created == []
    report = solution.report_definitions.volume[ACTIVE_WINDOW_VOLUME_REPORT]
    assert report.report_type == ACTIVE_WINDOW_VOLUME_REPORT_TYPE
    assert report.cell_zones == "solid"


def test_guards_reject_a_full_box_volume_an_oversized_membrane_and_a_missing_spacer():
    box = active_window_box_volume_m3(LENGTH, WIDTH, HEIGHT)
    projected = 2.0 * LENGTH * WIDTH
    with pytest.raises(RuntimeError, match="fluid volume"):
        require_active_window_geometry(
            fluid_volume_m3=box,
            membrane_area_m2=MEMBRANE_AREA,
            spacer_area_m2=0.001,
            box_volume_m3=box,
            active_length_m=LENGTH,
            periodic_shift_y_m=WIDTH,
            family="diamond",
        )
    with pytest.raises(RuntimeError, match="membrane area"):
        require_active_window_geometry(
            fluid_volume_m3=0.8 * box,
            membrane_area_m2=projected + 1.0e-9,
            spacer_area_m2=0.001,
            box_volume_m3=box,
            active_length_m=LENGTH,
            periodic_shift_y_m=WIDTH,
            family="diamond",
        )
    with pytest.raises(RuntimeError, match="spacer area"):
        require_active_window_geometry(
            fluid_volume_m3=0.8 * box,
            membrane_area_m2=projected,
            spacer_area_m2=0.0,
            box_volume_m3=box,
            active_length_m=LENGTH,
            periodic_shift_y_m=WIDTH,
            family="diamond",
        )
    porosity = require_active_window_geometry(
        fluid_volume_m3=0.8 * box,
        membrane_area_m2=projected,
        spacer_area_m2=0.0,
        box_volume_m3=box,
        active_length_m=LENGTH,
        periodic_shift_y_m=WIDTH,
        family="empty",
    )
    assert porosity == pytest.approx(0.8)


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
    assert "volume-zonevol" in description
    assert "sum_if" not in description
