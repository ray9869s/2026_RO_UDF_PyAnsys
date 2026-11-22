"""One-off c_b definition diagnostic: pure parts and refusals. No Fluent."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_module

DIAG = load_module("diagnose_c_b_definitions_test", SCRIPTS_DIR / "mfbo" / "diagnose_c_b_definitions.py")
FACTOR = 998.2 / 0.05844


def test_plane_plan_has_every_boundary_and_one_midcell_per_cell():
    plan = DIAG.plane_plan([0.0, 1.0, 3.0])
    assert plan == [("boundary", 0, 0.0), ("boundary", 1, 1.0), ("boundary", 2, 3.0),
                    ("midcell", 1, 0.5), ("midcell", 2, 2.0)]
    with pytest.raises(ValueError):
        DIAG.plane_plan([0.0, 0.0])


def test_plane_row_net_ratio_and_reduction_fallback():
    row = DIAG.plane_row("boundary", 6, 0.01, {
        "massavg": 0.0350, "flowrate": -0.0352e-3, "massflowrate": -1.0e-3,
        "reduction_salt_flow": 0.0351e-3, "reduction_mass_flow": 1.0e-3,
    }, [], factor=FACTOR)
    assert row["y_net_report"] == pytest.approx(0.0352)  # sign cancels
    assert row["y_net_reduction"] == pytest.approx(0.0351)
    assert row["c_net_mol_m3"] == pytest.approx(0.0352 * FACTOR)
    assert row["net_minus_massavg_rel"] == pytest.approx(0.0352 / 0.0350 - 1)
    fallback = DIAG.plane_row("boundary", 6, 0.01, {
        "massavg": 0.035, "reduction_salt_flow": 0.0351e-3, "reduction_mass_flow": 1.0e-3,
    }, ["flowrate: not allowed"], factor=FACTOR)
    assert fallback["y_net_report"] is None and fallback["c_net_mol_m3"] == pytest.approx(0.0351 * FACTOR)
    assert fallback["errors"] == "flowrate: not allowed"
    bad = DIAG.plane_row("boundary", 6, 0.01, {"flowrate": 1.0, "massflowrate": -1.0}, [], factor=FACTOR)
    assert bad["c_net_mol_m3"] is None


def test_cell_rows_compare_boundary_mean_with_midcell():
    planes = [
        DIAG.plane_row("boundary", 0, 0.0, {"massavg": 0.035, "flowrate": 0.035, "massflowrate": 1.0}, [], factor=1.0),
        DIAG.plane_row("boundary", 1, 1.0, {"massavg": 0.037, "flowrate": 0.038, "massflowrate": 1.0}, [], factor=1.0),
        DIAG.plane_row("midcell", 1, 0.5, {"massavg": 0.0359, "flowrate": 0.0364, "massflowrate": 1.0}, [], factor=1.0),
    ]
    (cell,) = DIAG.cell_rows(planes)
    assert cell["boundary_mean_massavg_mol_m3"] == pytest.approx(0.036)
    assert cell["massavg_boundary_minus_mid_rel"] == pytest.approx(0.036 / 0.0359 - 1)
    assert cell["boundary_mean_net_mol_m3"] == pytest.approx(0.0365)
    assert cell["net_boundary_minus_mid_rel"] == pytest.approx(0.0365 / 0.0364 - 1)


def test_refuses_production_copy_root_and_has_no_queue_option(tmp_path):
    with pytest.raises(ValueError, match="production"):
        DIAG.main(["--source", str(tmp_path / "runs/pillar/P_p80_h15/m/u0p05_p6M"),
                   "--data-root", "C:/ro_data/x"])
    assert "--emit-queue" not in DIAG.build_parser().format_help()


class _FakeSolver:
    """Records the call shape; every report type is allowed."""

    def __init__(self):
        self.calls = []
        outer = self

        class _Group(dict):
            def create(self, name):
                self[name] = _RD(name)

        class _RD:
            def __init__(self, name):
                self.name = name

            def set_state(self, state):
                outer.calls.append(("set_state", self.name, state["report_type"], state.get("field")))

        class _Defs:
            surface = _Group()

            def compute(self, report_defs):
                (name,) = report_defs
                return [{name: [0.5 if name.endswith("massflowrate") else 0.0175]}]

        class _Reduction:
            def mass_flow_integral(self, expression, locations):
                outer.calls.append(("reduction", expression))
                return 0.0176 if "MassFraction" in expression else 0.5

        class _Iso(dict):
            def __getitem__(self, name):
                return name

        self.settings = type("S", (), {})()
        self.settings.solution = type("Sol", (), {"report_definitions": _Defs()})()
        self.settings.results = type("R", (), {})()
        self.settings.results.surfaces = type("Su", (), {"iso_surface": _Iso()})()
        self.fields = type("F", (), {"reduction": _Reduction()})()


def test_evaluate_plane_call_shape(monkeypatch):
    import ro.fluent_report_helpers as helpers

    monkeypatch.setattr(helpers, "create_x_normal_plane", lambda solver, name, x: None)
    solver = _FakeSolver()
    values, errors = DIAG.evaluate_plane(solver, "boundary", 6, 0.01, allowed=None)
    assert errors == []
    assert values["flowrate"] / values["massflowrate"] == pytest.approx(0.035)
    assert values["reduction_salt_flow"] / values["reduction_mass_flow"] == pytest.approx(0.0352)
    types = {c[2] for c in solver.calls if c[0] == "set_state"}
    assert {"surface-flowrate", "surface-massflowrate", "surface-massavg"} <= types
    flow_field = [c[3] for c in solver.calls if c[0] == "set_state" and c[2] == "surface-massflowrate"]
    assert flow_field == [None]
    limited, errs = DIAG.evaluate_plane(_FakeSolver(), "boundary", 6, 0.01, allowed=["surface-massavg"])
    assert "flowrate" not in limited and any("not an allowed" in e for e in errs)
