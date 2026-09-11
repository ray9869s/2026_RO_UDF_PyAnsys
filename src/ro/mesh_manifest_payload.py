"""Build a mesh manifest payload without importing Fluent.

Live meshing and ``rebuild_mesh_manifest --from-log`` share this builder.
The worker stamps ``generator_version`` with its own filename; ``--from-log``
overwrites unrecoverable fields after the call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ro.campaign_geometry import merge_geometry_into_mesh_manifest
from ro.manifest import MANIFEST_SCHEMA_VERSION


def utc_now_string():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_mesh_manifest_payload(
    cfg,
    mesh_metrics,
    mesh_sha256,
    *,
    created_utc=None,
    generator_version=None,
):
    base = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "family": cfg.family,
        "geo_id": cfg.geo_id,
        "mesh_id": cfg.mesh_id,
        "spacing_code": cfg.spacing_code,
        "attack_angle_deg": cfg.attack_angle_deg,
        "filament_d_m": cfg.filament_d_m,
        "bridge_radius_m": cfg.bridge_radius_m,
        "overlap_m": cfg.overlap_m,
        "n_active_cells": cfg.n_active_cells,
        "n_buffer_in": cfg.n_buffer_in,
        "n_buffer_out": cfg.n_buffer_out,
        "cell_length_x_m": cfg.cell_length_x_m,
        # Config is millimetres (Fluent ShiftY); manifest stores metres.
        "periodic_shift_y_m": float(cfg.periodic_shift_y) * 1.0e-3,
        "buffer_length_in_m": cfg.buffer_length_in_m,
        "buffer_length_out_m": cfg.buffer_length_out_m,
        "membrane_wall_base_names": list(cfg.active_membrane_wall_labels),
        "buffer_wall_base_names": list(cfg.buffer_wall_labels),
        "n_lead_excluded": cfg.n_lead_excluded,
        "n_trail_excluded": cfg.n_trail_excluded,
        "max_size_mm": cfg.m_max,
        "min_size_mm": cfg.m_min,
        "cpg": cfg.m_cpg,
        "bl": cfg.bl_layers,
        "peel": cfg.peel_layers,
        "ortho_min": mesh_metrics["min_orthogonal_quality"],
        "AR_max": mesh_metrics["max_aspect_ratio"],
        "skewness_max": mesh_metrics["max_skewness"],
        "skewed_face_fraction": mesh_metrics["skewed_face_fraction"],
        "cell_count": mesh_metrics["cell_count"],
        "inlet_profile_G": None,
        "mesh_sha256": mesh_sha256,
        "created_utc": created_utc or utc_now_string(),
        "generator_version": (
            generator_version
            if generator_version is not None
            else Path(__file__).name
        ),
    }
    merged = merge_geometry_into_mesh_manifest(base, cfg.geo_id)
    # Measured porosity from fluid volume / bounding box; never a registry constant.
    measured_porosity = mesh_metrics.get("porosity")
    merged["porosity_eps"] = (
        float(measured_porosity) if measured_porosity is not None else None
    )
    # Measured extents from Fluent /mesh/check (mm → m in the parser). Optional:
    # not in MESH_MANIFEST_REQUIRED_FIELDS / _GEOMETRY_FIELDS. Copy the parse
    # result as-is (float or None). Do not substitute 0.0 for a missing parse,
    # and do not compute cell_length_x_m * n_total.
    for key in ("domain_extent_x_m", "domain_extent_y_m", "domain_extent_z_m"):
        merged[key] = mesh_metrics.get(key)
    return merged
