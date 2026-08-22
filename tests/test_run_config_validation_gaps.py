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
    load_solver_common,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)

_solver_common = load_solver_common()
merge_batch_case_overrides = _solver_common.merge_batch_case_overrides


@pytest.fixture
def cfg():
    module = load_run_config()
    populate_valid_meshing_config(module)
    populate_valid_solver_config(module)
    module.allow_legacy_mesh_case_name_mismatch = True
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
    def test_missing_manifest_metadata_is_rejected(self, cfg):
        cfg.bridge_radius_m = cfg.REQUIRED
        with pytest.raises(ValueError, match="bridge_radius_m"):
            cfg.validate_for_meshing()

    def test_malformed_manifest_layout_count_is_rejected(self, cfg):
        cfg.n_active_cells = 7.5
        with pytest.raises(TypeError, match="integer"):
            cfg.validate_for_meshing()

    def test_invalid_explicit_family_is_rejected(self, cfg):
        cfg.family = "Diamond"
        with pytest.raises(ValueError, match="family is invalid"):
            cfg.validate_for_meshing()

    def test_mesh_id_peel_token_must_match_peel_layers(self, cfg):
        cfg.peel_layers = 0
        with pytest.raises(ValueError, match="peel token must match"):
            cfg.validate_for_meshing()

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

    @pytest.mark.parametrize(
        "profile",
        ["baseline", "conservative", "strong"],
    )
    def test_relaxation_profiles_are_accepted(self, cfg, profile):
        cfg.relaxation_profile = profile
        cfg.validate_for_solver()

    def test_unknown_relaxation_profile_is_rejected(self, cfg):
        cfg.relaxation_profile = "aggressive"
        with pytest.raises(ValueError, match="must be one of"):
            cfg.validate_for_solver()

    def test_species_implicit_preserve_and_float_are_accepted(self, cfg):
        cfg.species_implicit_under_relaxation = "preserve"
        cfg.validate_for_solver()
        cfg.species_implicit_under_relaxation = 0.5
        cfg.validate_for_solver()

    def test_species_implicit_rejects_non_positive(self, cfg):
        cfg.species_implicit_under_relaxation = 0.0
        with pytest.raises(ValueError, match="species_implicit_under_relaxation"):
            cfg.validate_for_solver()

    def test_pseudo_time_verbosity_preserve_and_levels_are_accepted(self, cfg):
        cfg.pseudo_time_verbosity = "preserve"
        cfg.validate_for_solver()
        for level in (0, 1, 2):
            cfg.pseudo_time_verbosity = level
            cfg.validate_for_solver()

    def test_pseudo_time_verbosity_rejects_out_of_range(self, cfg):
        cfg.pseudo_time_verbosity = 3
        with pytest.raises(ValueError, match="pseudo_time_verbosity"):
            cfg.validate_for_solver()


def _load_batch_config_module(filename: str):
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(f"batch_{filename}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _campaign_solver_operating_cases():
    """(inlet_velocity, outlet_gauge_pressure, operating_pressure) from batch configs.

    Mirrors batch_solver_sweep production merge::

        overrides = {**common_solver_settings, **case_dict}

    so keys that live only in common_solver_settings (e.g. operating_pressure)
    remain visible without requiring every case dict to repeat them.
    """
    cases: list[tuple[float, float, float]] = []
    seen: set[tuple[float, float, float]] = set()

    for filename in (
        "batch_config.py",
        "batch_config_before_sin_3mesh_20260716_231030.py",
    ):
        batchcfg = _load_batch_config_module(filename)
        common = getattr(batchcfg, "common_solver_settings", {}) or {}
        for entry in getattr(batchcfg, "solver_sweep_cases", []):
            merged = merge_batch_case_overrides(common, entry)
            u = merged["inlet_velocity_value"]
            p_out = merged["outlet_gauge_pressure"]
            p_op = merged["operating_pressure"]
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
            "family",
            "geo_id",
            "mesh_id",
            "run_id",
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
            "max_skewness_threshold",
            "skewed_face_fraction_threshold",
            "fail_if_quality_not_parsed",
            "save_surface_mesh_checkpoint",
            "allow_legacy_mesh_case_name_mismatch",
            "inlet_velocity_value",
            "operating_pressure",
            "outlet_gauge_pressure",
            "template_case_file_name",
            "udf_source_file_name",
            "udf_library_name",
            "use_inlet_velocity_profile",
            "run_inlet_profile_probe",
            "debug_inlet_bc_api",
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
            "relaxation_profile",
            "species_implicit_under_relaxation",
            "pseudo_time_verbosity",
            "use_ramp_convergence_safety",
            "ramp_full_iteration",
            "post_ramp_buffer_iterations",
            "enable_qoi_convergence_stop",
            "qoi_convergence_report_name",
            "qoi_stop_criterion",
            "qoi_previous_values_to_consider",
            "qoi_initial_values_to_ignore",
            "enable_lmh_udm_avg_report_file",
            "lmh_udm_avg_report_file_name",
            "enable_pressure_drop_spacer_report_file",
            "pressure_drop_spacer_report_file_name",
            "update_rho_avg_report_definition",
            "rho_avg_report_name",
            "rho_avg_report_field",
            "area_mem_report_name",
            "lmh_report_name",
            "lmh_signed_report_name",
            "m_in_report_name",
            "m_out_report_name",
            "enable_solve_time_qoi_reports",
            "domain_x_min_m",
            "domain_length_m",
            "buffer_length_m",
        }

        excluded = {
            "os": "imported module",
            "ro_paths": "imported module",
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
            "_require_choice": "private function",
            "_require_preserve_or_positive_number": "private function",
            "_require_preserve_or_verbosity": "private function",
            "_require_bool": "private function",
            "_require_number": "private function",
        }

        assert extensions == {
            "family",
            "geo_id",
            "mesh_id",
            "run_id",
            "spacing_code",
            "attack_angle_deg",
            "filament_d_m",
            "bridge_radius_m",
            "overlap_m",
            "n_active_cells",
            "n_buffer_in",
            "n_buffer_out",
            "cell_length_x_m",
            "n_lead_excluded",
            "n_trail_excluded",
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
            merged = merge_batch_case_overrides(common_mesh, case)
            merged["case_name"] = merged.pop("mesh_case_name")
            override_keys.update(merged)

        common_solver = getattr(backup, "common_solver_settings", {})
        for case in getattr(backup, "solver_sweep_cases", []):
            override_keys.update(merge_batch_case_overrides(common_solver, case))
            override_keys.add("case_name")

        common_solver_current = getattr(current, "common_solver_settings", {})
        for case in getattr(current, "solver_sweep_cases", []):
            override_keys.update(
                merge_batch_case_overrides(common_solver_current, case)
            )
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
            "results_dir",
            "case_path",
            "rho",
            "mu",
            "c_inlet_ref",
            "salt_molecular_weight_kg_per_mol",
            "salt_permeability_m_per_s",
            "active_membrane_base_names",
            "buffer_wall_base_names",
            "udm_indices",
            "product_version",
            "processor_count",
            "graphics_driver",
            "fluent_start_timeout",
            "fluent_health_timeout",
            "domain_x_min_m",
            "n_buffer_in",
            "n_active",
            "n_buffer_out",
            "cell_length_x_m",
            "domain_length_m",
            "buffer_length_m",
            "n_unit_cells",
            "n_buffer_cells_each_end",
            "n_inlet_spacer_cells_excluded",
            "mesh_case_name",
            "mesh_resolution_source",
            "salt_mass_fraction_upper_threshold",
            "salt_mass_fraction_lower_threshold",
        }

        excluded = {
            "os": "imported module",
            "Path": "imported callable type",
            "_discover_project_root": "imported callable",
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
            "results_dir",
            "case_path",
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

    def test_asymmetric_layout_override_keys_are_allowed(self, post_cfg):
        """Live 01_batch path: layout keys must be admitted by the allowlist.

        apply_post_config_overrides → post_config_override_keys rejects any key
        not declared at module level (or in POST_CONFIG_OVERRIDE_EXTENSIONS).
        Before these keys were module-level, a live worker raised
        ValueError: Unknown post config override key(s).
        """
        overrides = {
            "n_buffer_in": 1,
            "n_active": 7,
            "n_buffer_out": 2,
            "cell_length_x_m": 0.003465,
            "domain_length_m": 0.03465,
            "buffer_length_m": 0.003465,
            "n_unit_cells": 10,
            "n_buffer_cells_each_end": None,
            "mesh_case_name": "mesh_max085_min006_cpg5_bl4",
            "mesh_resolution_source": "log",
            "active_membrane_base_names": ["wall_top_mem", "wall_bottom_mem"],
            "buffer_wall_base_names": [
                "wall_top_buffer_in",
                "wall_top_buffer_out",
                "wall_bottom_buffer_in",
                "wall_bottom_buffer_out",
            ],
        }
        apply_post_json_overrides(post_cfg, overrides)
        assert post_cfg.n_buffer_in == 1
        assert post_cfg.n_active == 7
        assert post_cfg.n_buffer_out == 2
        assert post_cfg.domain_length_m == 0.03465
        assert post_cfg.n_buffer_cells_each_end is None
        assert post_cfg.mesh_case_name == "mesh_max085_min006_cpg5_bl4"
