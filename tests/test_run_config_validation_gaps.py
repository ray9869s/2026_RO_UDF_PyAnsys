"""Tests for run_config validation gaps (F-04) and override hardening."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from helpers import (
    SCRIPTS_DIR,
    apply_json_overrides,
    apply_post_json_overrides,
    load_post_config,
    load_run_config,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)


@pytest.fixture
def cfg():
    module = load_run_config()
    populate_valid_meshing_config(module)
    populate_valid_solver_config(module)
    return module


@pytest.fixture
def post_cfg():
    module = load_post_config()
    module.project_root = "/tmp/project"
    module.geo_name = "Sin_ST"
    module.case_name = "u0p1_p4M__mesh_max100_min006_cpg3_bl3"
    return module


class TestNumericHelperGaps:
    def test_true_rejected_by_positive_number_check(self, cfg):
        with pytest.raises(TypeError, match="must be numeric, not bool"):
            cfg._require_positive_number("processor_count", True)

    def test_false_rejected_by_nonnegative_number_check(self, cfg):
        with pytest.raises(TypeError, match="must be numeric, not bool"):
            cfg._require_nonnegative_number("periodic_shift_x", False)


class TestMeshingValidation:
    def test_m_min_greater_than_m_max_is_rejected(self, cfg):
        cfg.m_max = 0.01
        cfg.m_min = 0.10
        with pytest.raises(ValueError, match="m_min must be <= m_max"):
            cfg.validate_for_meshing()

    def test_negative_mesh_sizes_are_rejected(self, cfg):
        cfg.m_min = -0.005
        with pytest.raises(ValueError, match="must be positive"):
            cfg.validate_for_meshing()

    @pytest.mark.parametrize(
        ("m_max", "m_min"),
        [
            (0.085, 0.005),
            (0.1, 0.006),
        ],
    )
    def test_campaign_mesh_size_pairs_are_accepted(self, cfg, m_max, m_min):
        cfg.m_max = m_max
        cfg.m_min = m_min
        cfg.validate_for_meshing()


class TestSolverValidation:
    def test_negative_inlet_velocity_is_rejected(self, cfg):
        cfg.inlet_velocity_value = -0.1
        with pytest.raises(ValueError, match="must be positive"):
            cfg.validate_for_solver()

    def test_string_numeric_inlet_velocity_is_accepted(self, cfg):
        cfg.inlet_velocity_value = "0.15"
        cfg.validate_for_solver()

    def test_outlet_gauge_pressure_negative_is_rejected(self, cfg):
        cfg.outlet_gauge_pressure = -1.0e6
        with pytest.raises(ValueError, match="must be non-negative"):
            cfg.validate_for_solver()

    def test_operating_pressure_negative_is_rejected(self, cfg):
        cfg.operating_pressure = -500.0
        with pytest.raises(ValueError, match="must be positive"):
            cfg.validate_for_solver()

    def test_salt_mass_fraction_above_one_is_rejected(self, cfg):
        cfg.salt_mass_fraction = 1.5
        with pytest.raises(ValueError, match="must be <= 1.0"):
            cfg.validate_for_solver()

    def test_salt_mass_fraction_zero_is_rejected(self, cfg):
        cfg.salt_mass_fraction = 0.0
        with pytest.raises(ValueError, match="must be positive"):
            cfg.validate_for_solver()

    def test_campaign_salt_mass_fraction_default_is_accepted(self, cfg):
        assert cfg.salt_mass_fraction == 0.035
        cfg.validate_for_solver()


def _load_batch_config_module(filename: str):
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(f"batch_{filename}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _campaign_solver_operating_cases():
    """(inlet_velocity, outlet_gauge_pressure, operating_pressure) from batch configs."""
    cases: list[tuple[float, float, float]] = []
    seen: set[tuple[float, float, float]] = set()

    for filename in (
        "batch_config.py",
        "batch_config_before_sin_3mesh_20260716_231030.py",
    ):
        batchcfg = _load_batch_config_module(filename)
        for entry in getattr(batchcfg, "solver_sweep_cases", []):
            u = entry["inlet_velocity_value"]
            p_out = entry["outlet_gauge_pressure"]
            p_op = entry["operating_pressure"]
            key = (float(u), float(p_out), float(p_op))
            if key not in seen:
                seen.add(key)
                cases.append(key)

    return cases


class TestCampaignSolverOperatingValues:
    @pytest.mark.parametrize(
        ("inlet_velocity", "outlet_gauge_pressure", "operating_pressure"),
        _campaign_solver_operating_cases(),
    )
    def test_campaign_u_p_pairs_pass_solver_validation(
        self,
        cfg,
        inlet_velocity,
        outlet_gauge_pressure,
        operating_pressure,
    ):
        cfg.inlet_velocity_value = inlet_velocity
        cfg.outlet_gauge_pressure = outlet_gauge_pressure
        cfg.operating_pressure = operating_pressure
        cfg.validate_for_solver()


class TestRunConfigOverrideAllowlist:
    def test_admitted_and_excluded_module_level_names(self, cfg):
        allowed = cfg.run_config_override_keys(cfg)
        extensions = cfg.RUN_CONFIG_OVERRIDE_EXTENSIONS

        admitted_config_fields = {
            "REQUIRED",
            "project_root",
            "geo_name",
            "case_name",
            "product_version",
            "processor_count",
            "graphics_driver",
            "fluent_start_timeout",
            "fluent_health_timeout",
            "m_max",
            "m_min",
            "m_cpg",
            "wall_spacer_labels",
            "active_membrane_wall_labels",
            "buffer_wall_labels",
            "periodic_labels",
            "periodic_reference_label",
            "periodic_shift_x",
            "periodic_shift_y",
            "periodic_shift_z",
            "boi_curvature_normal_angle",
            "boi_growth_rate",
            "bl_height_factor",
            "bl_layers",
            "bl_offset_method",
            "bl_growth_rate",
            "vol_hex_max_factor",
            "peel_layers",
            "min_orthogonal_quality_threshold",
            "max_aspect_ratio_threshold",
            "fail_if_quality_not_parsed",
            "save_surface_mesh_checkpoint",
            "inlet_velocity_value",
            "operating_pressure",
            "outlet_gauge_pressure",
            "template_case_file_name",
            "udf_source_file_name",
            "udf_library_name",
            "membrane_wall_base_names",
            "buffer_wall_base_names",
            "target_species_name",
            "salt_material_name",
            "salt_chemical_formula",
            "salt_mass_fraction",
            "salt_density",
            "salt_viscosity",
            "salt_molecular_weight",
            "mixture_name",
            "mixture_density",
            "mixture_viscosity",
            "mass_diffusivity",
            "udm_count",
            "residual_target",
            "max_iterations",
            "run_calculation_enabled",
            "use_ramp_convergence_safety",
            "ramp_full_iteration",
            "post_ramp_buffer_iterations",
            "update_rho_avg_report_definition",
            "rho_avg_report_name",
            "rho_avg_report_field",
            "area_mem_report_name",
            "lmh_report_name",
            "m_in_report_name",
            "m_out_report_name",
        }

        excluded = {
            "os": "imported module",
            "Path": "imported callable type",
            "validate_common": "public function",
            "validate_for_meshing": "public function",
            "validate_for_solver": "public function",
            "apply_run_config_overrides": "public function",
            "run_config_override_keys": "public function",
            "_is_blocked_override_target": "private function",
            "_is_unset": "private function",
            "_require_set": "private function",
            "_require_positive_number": "private function",
            "_require_nonnegative_number": "private function",
            "_require_positive_float": "private function",
        }

        assert extensions == {
            "mesh_case_name",
            "run_label",
            "restart_from_case_file",
            "restart_from_data_file",
        }
        assert admitted_config_fields <= allowed
        assert extensions <= allowed
        for name, reason in excluded.items():
            assert name not in allowed, f"{name} should be excluded ({reason})"

        # Path used as a config VALUE (not the imported Path type) must be allowed.
        cfg.custom_output_path = Path("/tmp/example.msh.h5")
        allowed_with_path_value = cfg.run_config_override_keys(cfg)
        assert "custom_output_path" in allowed_with_path_value


class TestRunConfigOverrideApplication:
    def test_unknown_override_keys_are_rejected(self, cfg):
        with pytest.raises(ValueError, match="Unknown run config override key"):
            apply_json_overrides(
                cfg,
                {
                    "totally_unknown_key": 123,
                    "another_typo": "value",
                },
            )

    def test_override_cannot_replace_callable(self, cfg):
        with pytest.raises(ValueError, match="Unknown run config override key"):
            apply_json_overrides(cfg, {"validate_for_meshing": object()})

    def test_json_override_roundtrip_matches_worker_pattern(self, cfg):
        overrides = {
            "m_max": 0.1,
            "m_min": 0.006,
            "max_iterations": 1000,
            "run_label": "preliminary_deadline_1000iter",
        }
        apply_json_overrides(cfg, json.loads(json.dumps(overrides)))
        assert cfg.m_max == 0.1
        assert cfg.m_min == 0.006
        assert cfg.max_iterations == 1000
        assert cfg.run_label == "preliminary_deadline_1000iter"
        cfg.validate_for_meshing()
        cfg.validate_for_solver()

    def test_campaign_override_keys_are_all_allowed(self, cfg):
        backup_path = SCRIPTS_DIR / "batch_config_before_sin_3mesh_20260716_231030.py"
        spec = importlib.util.spec_from_file_location("_backup_batch_config", backup_path)
        backup = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(backup)

        current_path = SCRIPTS_DIR / "batch_config.py"
        current_spec = importlib.util.spec_from_file_location("_current_batch_config", current_path)
        current = importlib.util.module_from_spec(current_spec)
        assert current_spec.loader is not None
        current_spec.loader.exec_module(current)

        allowed = cfg.run_config_override_keys(cfg)
        override_keys: set[str] = set()

        common_mesh = getattr(backup, "common_mesh_settings", {})
        for case in getattr(backup, "mesh_batch_cases", []):
            merged = {**common_mesh, **case}
            merged["case_name"] = merged.pop("mesh_case_name")
            override_keys.update(merged)

        common_solver = getattr(backup, "common_solver_settings", {})
        for case in getattr(backup, "solver_sweep_cases", []):
            override_keys.update({**common_solver, **case})
            override_keys.add("case_name")

        common_solver_current = getattr(current, "common_solver_settings", {})
        for case in getattr(current, "solver_sweep_cases", []):
            override_keys.update({**common_solver_current, **case})
            override_keys.add("case_name")

        override_keys.discard("base_case_name")
        missing = sorted(override_keys - allowed)
        assert not missing, f"Campaign override keys missing from allowlist: {missing}"


class TestPostConfigOverrideAllowlist:
    def test_admitted_and_excluded_module_level_names(self, post_cfg):
        allowed = post_cfg.post_config_override_keys(post_cfg)
        extensions = post_cfg.POST_CONFIG_OVERRIDE_EXTENSIONS

        admitted_config_fields = {
            "REQUIRED",
            "project_root",
            "geo_name",
            "case_name",
            "rho",
            "mu",
            "c_inlet_ref",
            "active_membrane_base_names",
            "buffer_wall_base_names",
            "udm_indices",
            "product_version",
            "processor_count",
            "graphics_driver",
            "fluent_start_timeout",
            "fluent_health_timeout",
            "domain_x_min_m",
            "domain_length_m",
            "buffer_length_m",
            "n_unit_cells",
            "n_buffer_cells_each_end",
        }

        excluded = {
            "os": "imported module",
            "Path": "imported callable type",
            "apply_post_config_overrides": "public function",
            "post_config_override_keys": "public function",
            "_is_blocked_override_target": "private function",
        }

        assert extensions == {
            "final_case_file",
            "final_data_file",
            "inlet_velocity_value",
            "outlet_gauge_pressure",
            "channel_height_m",
        }
        assert admitted_config_fields <= allowed
        assert extensions <= allowed
        for name, reason in excluded.items():
            assert name not in allowed, f"{name} should be excluded ({reason})"

        post_cfg.results_root = Path("/tmp/results")
        allowed_with_path_value = post_cfg.post_config_override_keys(post_cfg)
        assert "results_root" in allowed_with_path_value


class TestPostConfigOverrideApplication:
    def test_unknown_override_keys_are_rejected(self, post_cfg):
        with pytest.raises(ValueError, match="Unknown post config override key"):
            apply_post_json_overrides(post_cfg, {"not_a_post_key": 1})

    def test_report_batch_override_keys_are_allowed(self, post_cfg):
        overrides = {
            "geo_name": "Sin_ST",
            "case_name": "u0p1_p4M__mesh_max100_min006_cpg3_bl3",
            "final_case_file": "/tmp/final.cas.h5",
            "final_data_file": "/tmp/final.dat.h5",
            "inlet_velocity_value": 0.1,
            "outlet_gauge_pressure": 4.0e6,
        }
        apply_post_json_overrides(post_cfg, json.loads(json.dumps(overrides)))
        assert post_cfg.inlet_velocity_value == 0.1
        assert post_cfg.outlet_gauge_pressure == 4.0e6
