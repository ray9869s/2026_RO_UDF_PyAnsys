"""Why the production 3D UDF cannot be compiled into a 2D case.

``udfs/260822_RO_UDF.c`` stays byte-identical. The 2D session compiles
``udfs/260929_RO_UDF.c``. This module reads both files and still reports
the fences in the production source.
"""

from __future__ import annotations

from pathlib import Path

from ro.paths import udfs_dir

PRODUCTION_UDF_NAME = "260822_RO_UDF.c"
EXECUTION_UDF_NAME = "260929_RO_UDF.c"
EXECUTION_UDF_STATUS = "source_ready_fluent_unverified"

BLOCKER_RP_3D_FENCE = "rp_3d_compile_fence"
BLOCKER_Y1_THIRD_COMPONENT = "y1_uses_third_component"
BLOCKER_INLET_Z = "inlet_profile_uses_z"

_FENCE = '#error "260822 inlet profile / probe_inlet_profile require a 3D Fluent build."'
_Y1 = "(xc[2] - xf[2]) * Ar[2]"
_INLET_Z = "inlet_poiseuille_shape(x[2]"


def production_udf_path() -> Path:
    return udfs_dir() / PRODUCTION_UDF_NAME


def assess_udf_source(source: str) -> tuple[str, ...]:
    """Return blocker ids found in a UDF translation unit."""
    found: list[str] = []
    if _FENCE in source:
        found.append(BLOCKER_RP_3D_FENCE)
    if _Y1 in source:
        found.append(BLOCKER_Y1_THIRD_COMPONENT)
    if _INLET_Z in source:
        found.append(BLOCKER_INLET_Z)
    return tuple(found)


def assess_production_udf() -> tuple[str, ...]:
    path = production_udf_path()
    source = path.read_text(encoding="utf-8")
    blockers = assess_udf_source(source)
    if not blockers:
        raise RuntimeError(
            f"{path.name} no longer contains the known 2D compile fences."
        )
    return blockers


def execution_udf_path() -> Path:
    return udfs_dir() / EXECUTION_UDF_NAME


def assess_execution_udf() -> None:
    """Reject a 2D source that still indexes the 3D wall-normal axis."""
    path = execution_udf_path()
    source = path.read_text(encoding="ascii")
    if "#if RP_3D" not in source or "2D membrane source" not in source:
        raise RuntimeError(f"{path.name} is missing its 3D compile fence.")
    if "xc[2]" in source or "x[2]" in source or "Ar[2]" in source:
        raise RuntimeError(
            f"{path.name} still indexes component 2. That is the 3D z axis."
        )


def physics_record() -> dict[str, object]:
    blockers = assess_production_udf()
    assess_execution_udf()
    return {
        "dimension": 2,
        "flow": "steady_incompressible",
        "species": "nacl",
        "membrane_model": "solution_diffusion_udf",
        "production_udf": f"udfs/{PRODUCTION_UDF_NAME}",
        "execution_udf": f"udfs/{EXECUTION_UDF_NAME}",
        "udf_2d_status": EXECUTION_UDF_STATUS,
        "udf_2d_blockers": blockers,
    }
