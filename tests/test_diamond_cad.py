"""Diamond layout against the probed manual CAD. Does not launch Discovery."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.diamond_cad import (
    _BooleanDebug,
    _active_window_corners,
    _apply_boolean,
    _contact_component,
    _cutting_operations,
    _run_boolean,
    check_spacer_contacts,
    diamond_layout,
    filament_segments,
    generate_diamond_cad,
    nominal_areas_m2,
    sphere_centers,
)

# Probe of the nine manual .dsco files. Areas are mm^2. Outlet x and the
# spacer x-range are mm. Membrane areas match one diagonal contact band.
_MEASURED_MM = {
    "D2450_a30": {
        "inlet": 3.773192374224664,
        "membrane": 117.032439641212,
        "buffer_in": 16.97936685232163,
        "buffer_out": 33.95873370464324,
        "outlet_x": 35.85744476318359,
        "y_half": 2.450124979019165,
        "spacer_x": (3.465000152587891, 28.92744445800781),
    },
    "D2450_a45": {
        "inlet": 2.668050051403043,
        "membrane": 78.83016852596104,
        "buffer_in": 12.00622605743411,
        "buffer_out": 24.01245211486821,
        "outlet_x": 34.65000152587891,
        "y_half": 1.732500076293945,
        "spacer_x": (3.465000152587891, 27.72000122070312),
    },
    "D2450_a60": {
        "inlet": 2.178453493992606,
        "membrane": 65.0180095920054,
        "buffer_in": 9.803041397491148,
        "buffer_out": 19.60608819117761,
        "outlet_x": 34.8962516784668,
        "y_half": 1.414580225944519,
        "spacer_x": (3.465000152587891, 27.96624946594238),
    },
    "D1225_a30": {
        "inlet": 1.886596187112332,
        "membrane": 54.64626344964103,
        "buffer_in": 8.489683426160813,
        "buffer_out": 16.97936685232162,
        "outlet_x": 35.85744476318359,
        "y_half": 1.225062489509583,
        "spacer_x": (3.465000152587891, 28.92744445800781),
    },
    "D1225_a45": {
        "inlet": 1.334025025701521,
        "membrane": 36.80837714403475,
        "buffer_in": 6.00311302871705,
        "buffer_out": 12.00622605743411,
        "outlet_x": 34.65000152587891,
        "y_half": 0.8662500381469727,
        "spacer_x": (3.465000152587891, 27.72000122070312),
    },
    "D1225_a60": {
        "inlet": 1.089226746996303,
        "membrane": 30.3590294621151,
        "buffer_in": 4.901520698745571,
        "buffer_out": 9.803044095588803,
        "outlet_x": 34.8962516784668,
        "y_half": 0.7072901129722595,
        "spacer_x": (3.465000152587891, 27.96624946594238),
    },
    "D0817_a30": {
        "inlet": 1.257730791408221,
        "membrane": 33.85088461455905,
        "buffer_in": 5.659788950773875,
        "buffer_out": 11.31957790154775,
        "outlet_x": 35.85744476318359,
        "y_half": 0.8167083263397217,
        "spacer_x": (3.465000152587891, 28.92744445800781),
    },
    "D0817_a45": {
        "inlet": 0.8893499559402471,
        "membrane": 22.80110683272918,
        "buffer_in": 4.002075077104559,
        "buffer_out": 8.004150154209128,
        "outlet_x": 34.65000152587891,
        "y_half": 0.5774999856948853,
        "spacer_x": (3.465000152587891, 27.72000122070312),
    },
    "D0817_a60": {
        "inlet": 0.7261512105597774,
        "membrane": 18.80603525749348,
        "buffer_in": 3.267680672360451,
        "buffer_out": 6.535363143452855,
        "outlet_x": 34.8962516784668,
        "y_half": 0.4715267717838287,
        "spacer_x": (3.465000152587891, 27.96624946594238),
    },
}


def _diamond_ids():
    return [
        geo_id
        for geo_id in CAMPAIGN_GEO_ID_ORDER
        if family_for_geo_id(geo_id) == "diamond"
    ]


def test_layout_matches_the_nine_probed_domains_and_membrane_areas():
    assert list(_MEASURED_MM) == _diamond_ids()
    for geo_id, measured in _MEASURED_MM.items():
        layout = diamond_layout(geo_id)
        areas = nominal_areas_m2(layout)
        assert areas["inlet"] * 1.0e6 == pytest.approx(measured["inlet"], rel=1e-6)
        assert areas["outlet"] * 1.0e6 == pytest.approx(measured["inlet"], rel=1e-6)
        assert areas["wall_top_mem"] * 1.0e6 == pytest.approx(measured["membrane"], rel=1e-6)
        assert areas["wall_bottom_mem"] == pytest.approx(areas["wall_top_mem"])
        assert areas["wall_top_buffer_in"] * 1.0e6 == pytest.approx(
            measured["buffer_in"], rel=1e-6
        )
        assert areas["wall_top_buffer_out"] * 1.0e6 == pytest.approx(
            measured["buffer_out"], rel=1e-6
        )
        assert layout["x_outlet"] * 1.0e3 == pytest.approx(measured["outlet_x"], abs=1e-4)
        assert layout["y_max"] * 1.0e3 == pytest.approx(measured["y_half"], abs=1e-4)
        assert layout["z_max"] * 1.0e3 == pytest.approx(0.385, abs=1e-6)
        assert layout["x_active_0"] * 1.0e3 == pytest.approx(measured["spacer_x"][0], abs=1e-4)
        assert layout["x_active_1"] * 1.0e3 == pytest.approx(measured["spacer_x"][1], abs=1e-4)
        assert layout["buffer_in_m"] == pytest.approx(0.003465)
        assert layout["buffer_out_m"] == pytest.approx(0.00693)


def test_filaments_are_the_cell_diagonals_and_spheres_sit_on_the_crossings():
    layout = diamond_layout("D2450_a30")
    segments = filament_segments(layout)
    assert len(segments) == 2 * layout["n_active"]
    diagonal = (layout["pitch_m"] ** 2 + layout["periodic_dy_m"] ** 2) ** 0.5
    for index, segment in enumerate(segments):
        cell = index // 2
        start = segment["start"]
        direction = segment["direction"]
        end = tuple(
            start[axis] + diagonal * direction[axis]
            for axis in range(3)
        )
        x0 = layout["x_active_0"] + cell * layout["pitch_m"]
        x1 = x0 + layout["pitch_m"]
        product = direction[0] * direction[1]
        if segment["layer"] == "upper":
            assert start[1] == pytest.approx(layout["y_max"])
            assert start[2] == pytest.approx(layout["filament_radius_m"])
            assert end[1] == pytest.approx(layout["y_min"])
            assert product < 0.0
        else:
            assert start[1] == pytest.approx(layout["y_min"])
            assert start[2] == pytest.approx(-layout["filament_radius_m"])
            assert end[1] == pytest.approx(layout["y_max"])
            assert product > 0.0
        assert start[0] == pytest.approx(x0)
        assert end[0] == pytest.approx(x1)
        assert end[2] == pytest.approx(start[2])
        # The extruded cylinder begins one pitch before the active corner.
        assert segment["origin"][0] < layout["x_active_0"] or cell > 0

    centers = sphere_centers(layout)
    assert len(centers) == 3 * layout["n_active"] + 2
    assert all(center[2] == 0.0 for center in centers)
    for corner in _active_window_corners(layout):
        assert any(
            abs(center[0] - corner[0]) < 1e-9 and abs(center[1] - corner[1]) < 1e-9
            for center in centers
        )
    interior = [center for center in centers if abs(center[1]) < 1e-12]
    assert len(interior) == layout["n_active"]
    assert interior[0][0] == pytest.approx(layout["x_active_0"] + 0.5 * layout["pitch_m"])


def test_generated_axes_match_the_manual_layer_signs():
    probe = load_module(
        "probe_diamond_orientation_under_test",
        SCRIPTS_DIR / "_probe_reference_geometry.py",
    )
    for geo_id in _diamond_ids():
        cylinders = [
            {"origin": segment["start"], "direction": segment["direction"]}
            for segment in filament_segments(diamond_layout(geo_id))
        ]
        report = probe.filament_layer_report(cylinders)
        assert probe.filament_orientations_match(report, report) is True
        assert report["upper"]["dir_xy_sign"] == -1
        assert report["lower"]["dir_xy_sign"] == 1


def test_n_active_lengthens_only_the_active_section():
    short = diamond_layout("D2450_a45")
    long = diamond_layout("D2450_a45", n_active=short["n_active"] + 3)
    assert long["buffer_in_m"] == short["buffer_in_m"]
    assert long["buffer_out_m"] == short["buffer_out_m"]
    assert long["x_active_0"] == short["x_active_0"]
    assert long["x_active_1"] - long["x_active_0"] == pytest.approx(
        long["n_active"] * long["pitch_m"]
    )
    assert long["x_outlet"] - long["x_active_1"] == pytest.approx(short["buffer_out_m"])
    assert len(filament_segments(long)) == 2 * long["n_active"]


def test_generate_refuses_production_root_and_existing_files_before_discovery(tmp_path, monkeypatch):
    def boom():
        raise AssertionError("Discovery import was reached")

    monkeypatch.setattr("ro.diamond_cad._bind_geometry_symbols", boom)
    for out_dir in (
        "C:/ro_data",
        "C:/ro_data/geometries/diamond",
        r"C:\ro_data\mfbo",
        "/mnt/c/ro_data/study",
    ):
        with pytest.raises(ValueError, match="C:/ro_data"):
            generate_diamond_cad(geo_id="D2450_a45", out_dir=out_dir)

    existing = tmp_path / "D2450_a45.pmdb"
    existing.write_bytes(b"pmdb")
    with pytest.raises(FileExistsError, match="overwrite"):
        generate_diamond_cad(geo_id="D2450_a45", out_dir=tmp_path)
    with pytest.raises(ValueError, match="not a Diamond"):
        generate_diamond_cad(geo_id="P_p100_h30", out_dir=tmp_path)


def _centres_shifted_off_both_axes(layout):
    """Move one crossing off both filaments. The four corners stay put."""
    centers = list(sphere_centers(layout))
    x_m, y_m, z_m = centers[0]
    centers[0] = (x_m, y_m + 0.25 * (layout["y_max"] - layout["y_min"]), z_m)
    return centers


def test_sphere_centres_lie_on_both_layers_for_every_diamond_id():
    for geo_id in _diamond_ids():
        layout = diamond_layout(geo_id)
        with pytest.raises(RuntimeError, match="not on both filament axes"):
            check_spacer_contacts(layout, centers=_centres_shifted_off_both_axes(layout))
        contacts = check_spacer_contacts(layout)
        assert "filament_0" in contacts
        assert not any(contacts[name] == set() for name in contacts)
        assert len(sphere_centers(layout)) == 3 * layout["n_active"] + 2


def test_contact_check_runs_before_any_modeler_call(tmp_path, monkeypatch):
    def boom():
        raise AssertionError("Discovery import was reached")

    monkeypatch.setattr("ro.diamond_cad._bind_geometry_symbols", boom)
    monkeypatch.setattr("ro.diamond_cad.sphere_centers", _centres_shifted_off_both_axes)
    with pytest.raises(RuntimeError, match="not on both filament axes"):
        generate_diamond_cad(geo_id="D2450_a45", out_dir=tmp_path)


def test_seven_cell_domain_has_23_joint_spheres():
    layout = diamond_layout("D2450_a45")
    assert layout["n_active"] == 7
    centers = sphere_centers(layout)
    assert len(centers) == 23
    assert len(_active_window_corners(layout)) == 4


def _layer_names(layout):
    segments = filament_segments(layout)
    upper = [f"filament_{i}" for i, segment in enumerate(segments) if segment["layer"] == "upper"]
    lower = [f"filament_{i}" for i, segment in enumerate(segments) if segment["layer"] == "lower"]
    spheres = [f"sphere_{i}" for i in range(len(sphere_centers(layout)))]
    return upper, spheres, lower


def test_default_subtraction_removes_uppers_then_spheres_then_lowers():
    layout = diamond_layout("D2450_a45")
    upper, spheres, lower = _layer_names(layout)
    ops = _cutting_operations(
        unite_spacer=False,
        upper_names=upper,
        sphere_names=spheres,
        lower_names=lower,
        contacts={},
    )
    assert [(op, host) for op, host, _tool in ops] == [("subtract", "active")] * len(ops)
    assert [tool for _op, _host, tool in ops] == upper + spheres + lower


def test_unite_spacer_absorbs_spheres_before_any_lower_filament():
    layout = diamond_layout("D2450_a45")
    upper, spheres, lower = _layer_names(layout)
    contacts = check_spacer_contacts(layout)
    ops = _cutting_operations(
        unite_spacer=True,
        upper_names=upper,
        sphere_names=spheres,
        lower_names=lower,
        contacts=contacts,
    )
    tools = [tool for _op, _host, tool in ops]
    last_sphere = max(index for index, name in enumerate(tools) if name in spheres)
    first_lower = min(index for index, name in enumerate(tools) if name in lower)
    assert last_sphere < first_lower
    assert set(tools) == set(spheres + lower + upper[1:])
    assert all(op == "unite" for op, _host, _tool in ops)


def test_union_order_follows_the_contact_component():
    contacts = {
        "spacer": {"filament_1"},
        "filament_1": {"spacer", "sphere_0"},
        "sphere_0": {"filament_1", "filament_2"},
        "filament_2": {"sphere_0"},
    }
    order, pending = _contact_component("spacer", contacts)
    assert pending == []
    assert order.index("filament_1") < order.index("sphere_0")
    assert order.index("sphere_0") < order.index("filament_2")


class _FakeBody:
    def __init__(self, name, ident, volume=1.0e-9):
        self.name = name
        self.id = ident
        self.is_alive = True
        self._volume = volume
        self.unite_error = None

    @property
    def volume(self):
        if isinstance(self._volume, Exception):
            raise self._volume
        return self._volume

    def unite(self, other):
        if self.unite_error is not None and other.name == self.unite_error[0]:
            raise self.unite_error[1]
        other.is_alive = False

    def subtract(self, other):
        other.is_alive = False


class _FakeDesign:
    def __init__(self, bodies, name="D2450_a45"):
        self.bodies = bodies
        self.name = name
        self.exports = []

    def export_to_pmdb(self, location):
        path = Path(location) / f"{self.name}.pmdb"
        path.write_bytes(b"pmdb")
        self.exports.append(path)
        return path

    def export_to_scdocx(self, location):
        path = Path(location) / f"{self.name}.scdocx"
        path.write_bytes(b"scdocx")
        self.exports.append(path)
        return path


def _chain_design():
    bodies = [
        _FakeBody("spacer", "0:1"),
        _FakeBody("sphere_0", "0:2"),
        _FakeBody("filament_1", "0:3"),
    ]
    return _FakeDesign(bodies)


class _CuttingBody(_FakeBody):
    def unite(self, other):
        super().unite(other)
        self._volume += other._volume

    def subtract(self, other):
        super().subtract(other)
        self._volume -= 0.25 * other._volume


def test_each_subtract_keeps_one_fluid_body_and_a_smaller_volume():
    host = _CuttingBody("active", "0:1", volume=1.0e-6)
    tools = [
        _CuttingBody("filament_0", "0:2", volume=4.0e-9),
        _CuttingBody("sphere_0", "0:3", volume=2.0e-9),
        _CuttingBody("filament_1", "0:4", volume=4.0e-9),
    ]
    design = _FakeDesign([host, *tools])
    counter = [0]
    for tool in tools:
        _apply_boolean(
            design,
            "subtract",
            "active",
            tool.name,
            None,
            counter,
            fluid_body=True,
            volume_change="decrease",
        )
    alive = [body for body in design.bodies if body.is_alive]
    assert [body.name for body in alive] == ["active"]
    assert host.volume < 1.0e-6
    assert counter == [3]


def test_subtract_check_raises_with_the_op_index_when_volume_does_not_fall(tmp_path):
    host = _CuttingBody("active", "0:1", volume=1.0e-6)
    tool = _CuttingBody("filament_0", "0:2", volume=1.0e-9)

    def subtract(other):
        other.is_alive = False

    host.subtract = subtract
    design = _FakeDesign([host, tool])
    trace = _BooleanDebug(tmp_path, "D2450_a45")
    with pytest.raises(RuntimeError, match=r"op 1 subtract host=active tool=filament_0: fluid volume"):
        _apply_boolean(
            design,
            "subtract",
            "active",
            "filament_0",
            trace,
            [0],
            fluid_body=True,
            volume_change="decrease",
        )
    text = trace.log_path.read_text(encoding="utf-8")
    assert "op 1 subtract host=active tool=filament_0: fluid volume" in text
    assert trace.saved
    assert (tmp_path / "D2450_a45.pmdb").is_file()


def test_subtract_check_raises_when_a_separate_body_remains(tmp_path):
    host = _CuttingBody("active", "0:1", volume=1.0e-6)
    tool = _CuttingBody("filament_4", "0:4", volume=2.0e-9)
    leftover = _CuttingBody("temp", "0:9", volume=2.0e-9)
    leftover.is_alive = False

    def subtract(other):
        other.is_alive = False
        host._volume -= 1.0e-12
        leftover.is_alive = True

    host.subtract = subtract
    design = _FakeDesign([host, tool, leftover])
    with pytest.raises(RuntimeError, match=r"op 9 subtract host=active tool=filament_4: alive 2, expected 1"):
        _apply_boolean(
            design,
            "subtract",
            "active",
            "filament_4",
            None,
            [8],
            fluid_body=True,
            volume_change="decrease",
        )


def test_unite_check_raises_when_the_body_count_does_not_fall():
    host = _CuttingBody("filament_0", "0:1", volume=1.0e-8)
    tool = _CuttingBody("filament_1", "0:2", volume=1.0e-8)

    def unite(other):
        return

    host.unite = unite
    design = _FakeDesign([host, tool])
    with pytest.raises(RuntimeError, match=r"op 2 unite host=filament_0 tool=filament_1: alive 2, expected 1"):
        _apply_boolean(
            design,
            "unite",
            "filament_0",
            "filament_1",
            None,
            [1],
            fluid_body=False,
            volume_change="increase",
        )


def test_boolean_debug_logs_each_unite_without_saving_on_success(tmp_path):
    design = _chain_design()
    trace = _BooleanDebug(tmp_path, "D2450_a45")
    _run_boolean(design, "unite", "spacer", "sphere_0", trace)
    _run_boolean(design, "unite", "spacer", "filament_1", trace)
    text = trace.log_path.read_text(encoding="utf-8")
    assert "op 1 unite host=spacer tool=sphere_0 raised=no" in text
    assert "op 2 unite host=spacer tool=filament_1 raised=no" in text
    assert "body name=sphere_0 id=0:2 alive=False volume=1.000000e-09" in text
    assert "saved pmdb" not in text
    assert design.exports == []


def test_boolean_debug_saves_the_design_on_the_first_raised_unite(tmp_path):
    design = _chain_design()
    design.bodies[0].unite_error = (
        "filament_1",
        RuntimeError("geometry service connection terminated"),
    )
    trace = _BooleanDebug(tmp_path, "D2450_a45")
    _run_boolean(design, "unite", "spacer", "sphere_0", trace)
    with pytest.raises(RuntimeError, match="geometry service connection terminated"):
        _run_boolean(design, "unite", "spacer", "filament_1", trace)
    text = trace.log_path.read_text(encoding="utf-8")
    assert "op 1 unite host=spacer tool=sphere_0 raised=no" in text
    assert (
        "op 2 unite host=spacer tool=filament_1 raised=yes RuntimeError: "
        "geometry service connection terminated"
    ) in text
    assert "save reason: op 2 unite raised" in text
    assert f"saved pmdb: {tmp_path / 'D2450_a45.pmdb'}" in text
    assert f"saved scdocx: {tmp_path / 'D2450_a45.scdocx'}" in text
    assert (tmp_path / "D2450_a45.pmdb").is_file()
    assert (tmp_path / "D2450_a45.scdocx").is_file()
    assert trace.saved


def test_boolean_debug_records_a_dead_service_and_a_failed_save(tmp_path):
    host = _FakeBody("spacer", "0:1")
    tool = _FakeBody("sphere_0", "0:2")

    class _DeadDesign:
        def __init__(self):
            self.name = "D2450_a45"
            self.dead = False
            self._bodies = [host, tool]

        @property
        def bodies(self):
            if self.dead:
                raise RuntimeError("geometry service connection terminated")
            return self._bodies

        def export_to_pmdb(self, location):
            raise RuntimeError("geometry service connection terminated")

        def export_to_scdocx(self, location):
            raise RuntimeError("geometry service connection terminated")

    design = _DeadDesign()

    def unite(other):
        design.dead = True
        raise RuntimeError("geometry service connection terminated")

    host.unite = unite
    trace = _BooleanDebug(tmp_path, "D2450_a45")
    with pytest.raises(RuntimeError, match="connection terminated"):
        _run_boolean(design, "unite", "spacer", "sphere_0", trace)
    text = trace.log_path.read_text(encoding="utf-8")
    assert "raised=yes RuntimeError: geometry service connection terminated" in text
    assert "bodies unavailable: RuntimeError: geometry service connection terminated" in text
    assert "save pmdb failed: RuntimeError: geometry service connection terminated" in text
    assert "save scdocx failed: RuntimeError: geometry service connection terminated" in text
    assert not (tmp_path / "D2450_a45.pmdb").exists()


def test_boolean_debug_saves_when_more_than_one_body_remains(tmp_path):
    spacer = _FakeBody("spacer", "0:1")
    leftover = _FakeBody("temp", "0:9", volume=RuntimeError("no volume"))
    solid = _FakeBody("Solid", "0:10", volume=2.5e-8)
    design = _FakeDesign([spacer, leftover, solid])
    trace = _BooleanDebug(tmp_path, "D2450_a45")
    trace.note_final(design, spacer)
    text = trace.log_path.read_text(encoding="utf-8")
    assert "end alive=3 ok=no" in text
    assert "body name=temp id=0:9 alive=True volume=unavailable (RuntimeError)" in text
    assert "body name=Solid id=0:10 alive=True volume=2.500000e-08" in text
    assert "save reason: end alive=3 ok=no" in text
    assert (tmp_path / "D2450_a45.pmdb").is_file()
    assert (tmp_path / "D2450_a45.scdocx").is_file()
    trace.note_final(design, spacer)
    assert trace.log_path.read_text(encoding="utf-8").count("saved pmdb") == 1


def test_boolean_debug_log_is_created_before_discovery(tmp_path, monkeypatch):
    def boom():
        raise AssertionError("Discovery import was reached")

    monkeypatch.setattr("ro.diamond_cad._bind_geometry_symbols", boom)
    with pytest.raises(AssertionError, match="Discovery import was reached"):
        generate_diamond_cad(
            geo_id="D2450_a45",
            out_dir=tmp_path,
            debug_booleans=True,
        )
    text = (tmp_path / "D2450_a45_boolean_debug.log").read_text(encoding="utf-8")
    assert "geo_id=D2450_a45" in text
    assert not (tmp_path / "D2450_a45.pmdb").exists()


def test_debug_booleans_still_refuses_the_production_root(monkeypatch):
    def boom():
        raise AssertionError("Discovery import was reached")

    monkeypatch.setattr("ro.diamond_cad._bind_geometry_symbols", boom)
    with pytest.raises(ValueError, match="C:/ro_data"):
        generate_diamond_cad(
            geo_id="D2450_a45",
            out_dir="C:/ro_data/diamond_debug",
            debug_booleans=True,
        )


def test_debug_booleans_flag_is_forwarded(monkeypatch):
    script = load_module(
        "generate_diamond_cad_script",
        SCRIPTS_DIR / "generate_diamond_cad.py",
    )
    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return {"paths": {"pmdb": "D2450_a45.pmdb"}}

    monkeypatch.setattr("ro.diamond_cad.generate_diamond_cad", fake_generate)
    assert script.main(
        ["--geo-id", "D2450_a45", "--out-dir", "C:/temp/diamond_debug", "--debug-booleans"]
    ) == 0
    assert seen["debug_booleans"] is True
    assert seen["geo_id"] == "D2450_a45"
    assert script.main(
        ["--geo-id", "D2450_a45", "--out-dir", "C:/temp/diamond_debug"]
    ) == 0
    assert seen["debug_booleans"] is False
    assert seen["unite_spacer"] is False
    assert script.main(
        [
            "--geo-id",
            "D2450_a45",
            "--out-dir",
            "C:/temp/diamond_debug",
            "--unite-spacer",
        ]
    ) == 0
    assert seen["unite_spacer"] is True


def test_import_does_not_launch_discovery():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "ro" / "diamond_cad.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    launched = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_bind_geometry_symbols":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and getattr(child.func, "id", None) == (
                "launch_modeler_with_discovery"
            ):
                launched.append(node.name)
    assert launched == ["generate_diamond_cad"]
