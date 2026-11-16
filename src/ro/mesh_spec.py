"""Campaign v3 mesh spec.

``configs/mesh_spec_v3.json`` is one object per family. A family entry
overrides ``defaults`` field by field. The smooth-transition ``bl`` token
stays 4; the membrane stack is the last-ratio count at the requested y1.

The SHA-256 of the file bytes and the git HEAD that launches the job are
stamped on the mesh and run manifests.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER
from ro.paths import project_root
from ro.solver_common import sha256_file

SPEC_FIELDS = (
    "m_max",
    "m_min",
    "cpg",
    "membrane_bl_y1_um",
    "membrane_bl_offset_method",
    "bulk_max_size_mm",
    "peel",
)
FAMILIES = ("diamond", "empty", "ml", "pillar", "sin")
OFFSET_METHOD = "last-ratio"
MEMBRANE_FACE_LABELS = ("wall_top_mem", "wall_bottom_mem")
MEMBRANE_FACE_GROWTH_RATE = 1.2
# Smooth-transition mesh-id token. The membrane layer count is derived
# from y1 and m_max; this token is not a family knob.
BL_LAYERS = 4
_GIT_HEAD_RE_TEXT = r"^[0-9a-f]{40}$"
_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12


def default_spec_path() -> Path:
    return project_root() / "configs" / "mesh_spec_v3.json"


def resolve_spec_path(value) -> Path:
    """Absolute spec path. A relative path is under the repository root."""
    if not isinstance(value, (str, Path)) or str(value).strip() == "":
        raise ValueError(f"mesh spec path is required, got {value!r}.")
    path = Path(value)
    if not path.is_absolute():
        path = project_root() / path
    if not path.is_file():
        raise FileNotFoundError(f"mesh spec not found: {path}")
    return path


def spec_file_sha256(path) -> str:
    return sha256_file(path)


def load_mesh_spec(path) -> dict:
    """Read and check the spec. Family entries may be empty."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"mesh spec must be a JSON object: {path}")
    unknown = sorted(set(payload) - {"defaults", "families", "geometries"})
    if unknown or "defaults" not in payload or "families" not in payload:
        raise ValueError(
            "mesh spec must contain defaults and families, "
            f"got {sorted(payload)}."
        )
    defaults = _require_complete("defaults", payload["defaults"])
    families = payload["families"]
    if not isinstance(families, dict) or set(families) != set(FAMILIES):
        raise ValueError(
            f"mesh spec families must be {list(FAMILIES)}, "
            f"got {sorted(families) if isinstance(families, dict) else families!r}."
        )
    resolved = {"defaults": defaults, "families": {}}
    for family in FAMILIES:
        entry = families[family]
        if not isinstance(entry, dict):
            raise ValueError(f"mesh spec family {family} must be an object.")
        unknown_fields = sorted(set(entry) - set(SPEC_FIELDS))
        if unknown_fields:
            raise ValueError(
                f"mesh spec family {family} has unknown fields: {unknown_fields}."
            )
        merged = dict(defaults)
        merged.update(_checked_fields(family, entry, partial=True))
        _check_pair(family, merged)
        resolved["families"][family] = merged
    resolved["geometries"] = _geometry_face_sizes(
        payload.get("geometries", {})
    )
    return resolved


def _geometry_face_sizes(value) -> dict:
    """Per-geometry membrane face size. Omitted or ``{}`` changes nothing."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            f"mesh spec geometries must be an object, got {value!r}."
        )
    allowed = set(CAMPAIGN_GEO_ID_ORDER)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"mesh spec geometries not in the campaign: {unknown}."
        )
    resolved = {}
    for geo_id, entry in value.items():
        if not isinstance(entry, dict) or set(entry) != {"membrane_face_size_mm"}:
            raise ValueError(
                "mesh spec geometry "
                f"{geo_id} must be an object with membrane_face_size_mm, "
                f"got {entry!r}."
            )
        size = entry["membrane_face_size_mm"]
        if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
            raise ValueError(
                f"{geo_id} membrane_face_size_mm must be positive, got {size!r}."
            )
        resolved[geo_id] = float(size)
    return resolved


def geometry_membrane_face_size(spec, geo_id):
    """Local face-size override for one geometry, or None when unset.

    The size is recorded on the mesh manifest as ``local_face_size``.
    Growth rate 1.2 is the campaign default and is omitted from the mesh id.
    """
    size = (spec.get("geometries") or {}).get(geo_id)
    if size is None:
        return None
    return {
        "labels": list(MEMBRANE_FACE_LABELS),
        "size_mm": float(size),
        "growth_rate": MEMBRANE_FACE_GROWTH_RATE,
    }


def family_settings(spec, family) -> dict:
    if family not in FAMILIES:
        raise ValueError(f"mesh spec has no family {family!r}.")
    return dict(spec["families"][family])


def changes_for_settings(settings) -> dict:
    """Knobs ``mesh_study_case`` applies for one resolved family."""
    return {
        "m_max": float(settings["m_max"]),
        "m_min": float(settings["m_min"]),
        "m_cpg": int(settings["cpg"]),
        "bl_layers": BL_LAYERS,
        "membrane_bl_y1_um": float(settings["membrane_bl_y1_um"]),
        "bulk_max_size_mm": float(settings["bulk_max_size_mm"]),
        "peel_layers": int(settings["peel"]),
    }


def require_changes_match(changes, settings) -> None:
    """Refuse a job whose knobs are not this family's spec."""
    expected = changes_for_settings(settings)
    for key, value in expected.items():
        if key not in changes or not _same(changes[key], value):
            got = changes.get(key, None)
            raise ValueError(
                f"{key} is {got!r}; the mesh spec requires {value!r}."
            )
    if settings["membrane_bl_offset_method"] != OFFSET_METHOD:
        raise ValueError(
            "membrane BL mode must be last-ratio, got "
            f"{settings['membrane_bl_offset_method']!r}."
        )


def read_git_head(repo=None) -> str:
    """Full lowercase git HEAD. Raises when git cannot say."""
    root = project_root() if repo is None else Path(repo)
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git rev-parse HEAD failed: {detail}")
    sha = result.stdout.strip()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError(f"git HEAD is not a full revision: {sha!r}.")
    return sha


def provenance_fields(spec_path, *, git_head=None) -> dict:
    """Manifest fields for one launch. Git HEAD is read at call time."""
    return {
        "mesh_spec_sha256": spec_file_sha256(spec_path),
        "git_head": read_git_head() if git_head is None else _require_git_head(git_head),
    }


def _require_git_head(value) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(f"git_head must be a full revision, got {value!r}.")
    if any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"git_head must be a full revision, got {value!r}.")
    return value


def _require_complete(label, value) -> dict:
    if not isinstance(value, dict) or set(value) != set(SPEC_FIELDS):
        raise ValueError(
            f"mesh spec {label} must contain {list(SPEC_FIELDS)}, "
            f"got {sorted(value) if isinstance(value, dict) else value!r}."
        )
    checked = _checked_fields(label, value, partial=False)
    _check_pair(label, checked)
    return checked


def _checked_fields(label, value, *, partial) -> dict:
    checked = {}
    for key in SPEC_FIELDS:
        if key not in value:
            if partial:
                continue
            raise ValueError(f"mesh spec {label} is missing {key}.")
        checked[key] = _check_field(label, key, value[key])
    return checked


def _check_field(label, key, value):
    if key in ("m_max", "m_min", "membrane_bl_y1_um", "bulk_max_size_mm"):
        return _positive_float(f"{label}.{key}", value)
    if key == "cpg":
        return _positive_int(f"{label}.{key}", value)
    if key == "peel":
        return _nonnegative_int(f"{label}.{key}", value)
    if key == "membrane_bl_offset_method":
        if value != OFFSET_METHOD:
            raise ValueError(
                f"mesh spec {label}.{key} must be {OFFSET_METHOD!r}, got {value!r}."
            )
        return value
    raise ValueError(f"mesh spec field {key!r} is unknown.")


def _check_pair(label, settings) -> None:
    if settings["m_min"] > settings["m_max"]:
        raise ValueError(
            f"mesh spec {label} m_min must be <= m_max. "
            f"m_min={settings['m_min']!r}, m_max={settings['m_max']!r}."
        )


def _positive_float(label, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"mesh spec {label} must be a positive number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"mesh spec {label} must be a positive number, got {value!r}.")
    return number


def _positive_int(label, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"mesh spec {label} must be an integer >= 1, got {value!r}.")
    return value


def _nonnegative_int(label, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"mesh spec {label} must be an integer >= 0, got {value!r}.")
    return value


def _same(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=_FLOAT_REL_TOL,
            abs_tol=_FLOAT_ABS_TOL,
        )
    return left == right
