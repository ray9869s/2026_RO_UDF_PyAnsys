"""Pure helpers for meshing metrics and structured run ledgers."""

from __future__ import annotations

import ast
import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


MESH_PARAMETER_NAMES = (
    "product_version",
    "processor_count",
    "graphics_driver",
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
    "boundary_layer_labels",
    "bl_height_factor",
    "bl_height",
    "bl_layers",
    "bl_offset_method",
    "bl_growth_rate",
    "vol_hex_max_factor",
    "vol_hex_max",
    "peel_layers",
    "min_orthogonal_quality_threshold",
    "max_aspect_ratio_threshold",
    "fail_if_quality_not_parsed",
    "save_surface_mesh_checkpoint",
    "allow_legacy_mesh_case_name_mismatch",
)

MESH_METRIC_NAMES = (
    "min_orthogonal_quality",
    "max_aspect_ratio",
    "max_skewness",
    "cell_count",
    "domain_extent_x_m",
    "domain_extent_y_m",
    "domain_extent_z_m",
    "min_cell_volume_m3",
    "max_cell_volume_m3",
    "total_fluid_volume_m3",
    "bounding_box_volume_m3",
    "porosity",
)

# Geometry is imported with LengthUnit="mm"; /mesh/check prints mm and mm^3.
_MESH_CHECK_LENGTH_TO_M = 1.0e-3
_MESH_CHECK_VOLUME_TO_M3 = 1.0e-9
_POROSITY_CLAMP_TOLERANCE = 1.0e-6

_FLOAT_TOKEN = r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_SURFACE_SKEWNESS_HEADER = re.compile(
    r"^[ \t]*name[ \t]+skewed-cells[ \t]+\("
    r">[ \t]*0\.80\)[ \t]+averaged-skewness[ \t]+"
    r"maximum-skewness[ \t]+face[ \t]+count[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_SURFACE_SKEWNESS_ROW = re.compile(
    rf"^[ \t]*(?P<name>\S+)[ \t]+"
    rf"(?P<skewed_cells>[\d,]+)[ \t]+"
    rf"(?P<average>{_FLOAT_TOKEN})[ \t]+"
    rf"(?P<maximum>{_FLOAT_TOKEN})[ \t]+"
    rf"(?P<face_count>[\d,]+)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_SURFACE_SKEWNESS_SUMMARY = re.compile(
    rf"^[ \t-]*Surface[ \t]+Meshing[^\r\n]*?"
    rf"maximum[ \t]+skewness[ \t]+of[ \t]+"
    rf"(?P<maximum>{_FLOAT_TOKEN})[ \t]*\.?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_CREATED_CELL_COUNT = re.compile(
    rf"^[ \t]*-+[ \t]*(?P<count>[\d,]+)[ \t]+"
    rf"cells[ \t]+were[ \t]+created[ \t]+in[ \t]*:[ \t]*"
    rf"{_FLOAT_TOKEN}[ \t]+minutes?[ \t]*\.?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_DOMAIN_EXTENTS = re.compile(
    rf"^[ \t]*Domain[ \t]+extents\.[ \t]*\r?\n"
    rf"^[ \t]*x-coordinate[ \t]*:[ \t]*min[ \t]*=[ \t]*(?P<x_min>{_FLOAT_TOKEN})"
    rf"[ \t]*,[ \t]*max[ \t]*=[ \t]*(?P<x_max>{_FLOAT_TOKEN})[ \t]*\.?[ \t]*\r?\n"
    rf"^[ \t]*y-coordinate[ \t]*:[ \t]*min[ \t]*=[ \t]*(?P<y_min>{_FLOAT_TOKEN})"
    rf"[ \t]*,[ \t]*max[ \t]*=[ \t]*(?P<y_max>{_FLOAT_TOKEN})[ \t]*\.?[ \t]*\r?\n"
    rf"^[ \t]*z-coordinate[ \t]*:[ \t]*min[ \t]*=[ \t]*(?P<z_min>{_FLOAT_TOKEN})"
    rf"[ \t]*,[ \t]*max[ \t]*=[ \t]*(?P<z_max>{_FLOAT_TOKEN})[ \t]*\.?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_VOLUME_STATISTICS = re.compile(
    rf"^[ \t]*Volume[ \t]+statistics\.[ \t]*\r?\n"
    rf"^[ \t]*minimum[ \t]+volume[ \t]*:[ \t]*(?P<minimum>{_FLOAT_TOKEN})"
    rf"[ \t]*\.?[ \t]*\r?\n"
    rf"^[ \t]*maximum[ \t]+volume[ \t]*:[ \t]*(?P<maximum>{_FLOAT_TOKEN})"
    rf"[ \t]*\.?[ \t]*\r?\n"
    rf"^[ \t]*total[ \t]+volume[ \t]*:[ \t]*(?P<total>{_FLOAT_TOKEN})"
    rf"[ \t]*\.?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

MESH_LEDGER_FIELDNAMES = (
    "geo_name",
    "mesh_case_name",
    "canonical_mesh_case_name",
    "mesh_case_name_validation",
    *MESH_PARAMETER_NAMES,
    "status",
    "exit_code",
    "wall_time_seconds",
    *MESH_METRIC_NAMES,
    "quality_gate_passed",
    "mesh_log_path",
    "mesh_file_path",
    "error_summary",
    "recorded_at_utc",
)

_FLOAT_PATTERN = r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"


def _integer_token(name, value, *, scale=1.0, width=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric, got {value!r}.")
    scaled = float(value) * scale
    rounded = round(scaled)
    if scaled <= 0.0 or abs(scaled - rounded) > 1.0e-9:
        raise ValueError(
            f"{name} cannot be represented by the canonical integer token: "
            f"{value!r}."
        )
    return f"{int(rounded):0{width}d}"


def make_canonical_mesh_case_name(m_max, m_min, m_cpg, bl_layers):
    """Construct the canonical mesh folder name from encoded parameters."""
    return (
        "mesh_"
        f"max{_integer_token('m_max', m_max, scale=1000.0, width=3)}_"
        f"min{_integer_token('m_min', m_min, scale=1000.0, width=3)}_"
        f"cpg{_integer_token('m_cpg', m_cpg)}_"
        f"bl{_integer_token('bl_layers', bl_layers)}"
    )


def assert_mesh_case_name_matches(
    mesh_case_name,
    m_max,
    m_min,
    m_cpg,
    bl_layers,
    *,
    allow_legacy=False,
):
    """Raise when a supplied mesh name disagrees with its encoded parameters."""
    if not isinstance(allow_legacy, bool):
        raise TypeError(
            f"allow_legacy must be bool, got {allow_legacy!r}."
        )
    expected = make_canonical_mesh_case_name(
        m_max,
        m_min,
        m_cpg,
        bl_layers,
    )
    if mesh_case_name != expected and not allow_legacy:
        raise AssertionError(
            "mesh_case_name does not match the supplied mesh parameters: "
            f"supplied={mesh_case_name!r}, canonical={expected!r}. "
            "Set allow_legacy_mesh_case_name_mismatch=True only for "
            "known legacy cases."
        )
    return expected


def mesh_case_name_provenance(mesh_case_name, mesh_parameters):
    """Return canonical name and validation status for a ledger record."""
    required = ("m_max", "m_min", "m_cpg", "bl_layers")
    if any(mesh_parameters.get(name) is None for name in required):
        return None, "UNAVAILABLE"
    try:
        canonical = make_canonical_mesh_case_name(
            *(mesh_parameters[name] for name in required)
        )
    except (TypeError, ValueError):
        return None, "UNAVAILABLE"
    if mesh_case_name == canonical:
        return canonical, "MATCH"
    if mesh_parameters.get("allow_legacy_mesh_case_name_mismatch"):
        return canonical, "LEGACY_OPT_OUT"
    return canonical, "MISMATCH"


def parse_last_float(pattern, text, flags=0):
    """Return the final float captured by a regex, or None."""
    matches = re.findall(pattern, text, flags)
    if not matches:
        return None
    value = matches[-1]
    if isinstance(value, tuple):
        value = value[-1]
    return float(str(value).replace(",", ""))


def _parse_last_int(pattern, text, flags=0):
    matches = re.findall(pattern, text, flags)
    if not matches:
        return None
    value = matches[-1]
    if isinstance(value, tuple):
        value = value[-1]
    return int(str(value).replace(",", ""))


def _parse_surface_table_max_skewness(text):
    """Return full-precision max skewness from surface skewness tables."""
    headers = list(_SURFACE_SKEWNESS_HEADER.finditer(text))
    if not headers:
        return None

    unique_rows = {}
    for index, header in enumerate(headers):
        start = header.end()
        end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else len(text)
        )
        block = text[start:end]
        for match in _SURFACE_SKEWNESS_ROW.finditer(block):
            key = (
                match.group("skewed_cells"),
                match.group("average"),
                match.group("maximum"),
                match.group("face_count"),
            )
            unique_rows[key] = float(match.group("maximum"))
    if not unique_rows:
        return None
    return max(unique_rows.values())


def _parse_surface_summary_max_skewness(text):
    matches = list(_SURFACE_SKEWNESS_SUMMARY.finditer(text))
    if not matches:
        return None
    return float(matches[-1].group("maximum"))


def _parse_created_cell_count(text):
    matches = list(_CREATED_CELL_COUNT.finditer(text))
    if not matches:
        return None
    return int(matches[-1].group("count").replace(",", ""))


def _parse_domain_extents_m(text):
    matches = list(_DOMAIN_EXTENTS.finditer(text))
    if not matches:
        return None, None, None
    match = matches[-1]
    return (
        (float(match.group("x_max")) - float(match.group("x_min")))
        * _MESH_CHECK_LENGTH_TO_M,
        (float(match.group("y_max")) - float(match.group("y_min")))
        * _MESH_CHECK_LENGTH_TO_M,
        (float(match.group("z_max")) - float(match.group("z_min")))
        * _MESH_CHECK_LENGTH_TO_M,
    )


def _parse_volume_statistics_m3(text):
    matches = list(_VOLUME_STATISTICS.finditer(text))
    if not matches:
        return None, None, None
    match = matches[-1]
    return (
        float(match.group("minimum")) * _MESH_CHECK_VOLUME_TO_M3,
        float(match.group("maximum")) * _MESH_CHECK_VOLUME_TO_M3,
        float(match.group("total")) * _MESH_CHECK_VOLUME_TO_M3,
    )


def _derive_bounding_box_and_porosity(
    extent_x_m,
    extent_y_m,
    extent_z_m,
    total_fluid_volume_m3,
):
    if None in (extent_x_m, extent_y_m, extent_z_m):
        return None, None
    if min(extent_x_m, extent_y_m, extent_z_m) <= 0.0:
        return None, None
    bounding_box_volume_m3 = extent_x_m * extent_y_m * extent_z_m
    if total_fluid_volume_m3 is None or bounding_box_volume_m3 <= 0.0:
        return bounding_box_volume_m3, None
    porosity = total_fluid_volume_m3 / bounding_box_volume_m3
    if abs(porosity) <= _POROSITY_CLAMP_TOLERANCE:
        porosity = 0.0
    elif abs(porosity - 1.0) <= _POROSITY_CLAMP_TOLERANCE:
        porosity = 1.0
    return bounding_box_volume_m3, porosity


def parse_mesh_metrics_text(text):
    """Parse final volume-mesh quality metrics from a Fluent transcript."""
    min_orthogonal_quality = parse_last_float(
        rf"Minimum\s+Orthogonal\s+Quality\s*=\s*{_FLOAT_PATTERN}",
        text,
        re.IGNORECASE,
    )
    if min_orthogonal_quality is None:
        min_orthogonal_quality = parse_last_float(
            rf"minimum\s+Orthogonal\s+Quality\s+of:\s*{_FLOAT_PATTERN}",
            text,
            re.IGNORECASE,
        )

    max_aspect_ratio = parse_last_float(
        rf"Maximum\s+Aspect\s+Ratio\s*=\s*{_FLOAT_PATTERN}",
        text,
        re.IGNORECASE,
    )

    max_skewness = _parse_surface_table_max_skewness(text)
    if max_skewness is None:
        max_skewness = _parse_surface_summary_max_skewness(text)
    if max_skewness is None:
        max_skewness = parse_last_float(
            rf"Maximum(?:\s+Cell)?\s+Skewness\s*(?:=|:)\s*{_FLOAT_PATTERN}",
            text,
            re.IGNORECASE,
        )

    cell_count = _parse_created_cell_count(text)
    if cell_count is None:
        cell_count_patterns = (
            r"Total\s+Number\s+of\s+Cells\s*(?:=|:)\s*([\d,]+)",
            r"Number\s+of\s+Cells\s*(?:=|:)\s*([\d,]+)",
            r"^\s*([\d,]+)\s+cells\b",
        )
        for pattern in cell_count_patterns:
            parsed = _parse_last_int(
                pattern,
                text,
                re.IGNORECASE | re.MULTILINE,
            )
            if parsed is not None:
                cell_count = parsed
                break

    (
        domain_extent_x_m,
        domain_extent_y_m,
        domain_extent_z_m,
    ) = _parse_domain_extents_m(text)
    (
        min_cell_volume_m3,
        max_cell_volume_m3,
        total_fluid_volume_m3,
    ) = _parse_volume_statistics_m3(text)
    (
        bounding_box_volume_m3,
        porosity,
    ) = _derive_bounding_box_and_porosity(
        domain_extent_x_m,
        domain_extent_y_m,
        domain_extent_z_m,
        total_fluid_volume_m3,
    )

    return {
        "min_orthogonal_quality": min_orthogonal_quality,
        "max_aspect_ratio": max_aspect_ratio,
        "max_skewness": max_skewness,
        "cell_count": cell_count,
        "domain_extent_x_m": domain_extent_x_m,
        "domain_extent_y_m": domain_extent_y_m,
        "domain_extent_z_m": domain_extent_z_m,
        "min_cell_volume_m3": min_cell_volume_m3,
        "max_cell_volume_m3": max_cell_volume_m3,
        "total_fluid_volume_m3": total_fluid_volume_m3,
        "bounding_box_volume_m3": bounding_box_volume_m3,
        "porosity": porosity,
    }


def parse_mesh_metrics_from_log(log_path):
    """Read and parse a Fluent meshing transcript."""
    log_path = Path(log_path)
    if not log_path.is_file():
        return {name: None for name in MESH_METRIC_NAMES}
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    return parse_mesh_metrics_text(text)


def _last_line_value(text, label):
    pattern = rf"^\s*{re.escape(label)}\s*:\s*(.*?)\s*$"
    matches = re.findall(pattern, text, re.MULTILINE)
    return matches[-1] if matches else None


def _parse_scalar(value, value_type):
    if value is None:
        return None
    if value_type is str:
        return value.strip()
    if value_type is int:
        return int(float(value.strip()))
    if value_type is float:
        return float(value.strip())
    if value_type is bool:
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        return None
    if value_type is list:
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            return None
        return list(parsed) if isinstance(parsed, (list, tuple)) else None
    return None


def parse_meshing_input_summary(text):
    """Recover mesh parameters printed by current worker transcripts."""
    label_types = {
        "Maximum size, m_max [mm]": ("m_max", float),
        "Minimum size, m_min [mm]": ("m_min", float),
        "Cells per gap, m_cpg [-]": ("m_cpg", int),
        "Active membrane wall labels": (
            "active_membrane_wall_labels",
            list,
        ),
        "Buffer wall labels": ("buffer_wall_labels", list),
        "Wall spacer labels for local sizing": (
            "wall_spacer_labels",
            list,
        ),
        "Periodic labels": ("periodic_labels", list),
        "Periodic reference label": ("periodic_reference_label", str),
        "BOI curvature normal angle [deg]": (
            "boi_curvature_normal_angle",
            float,
        ),
        "BOI growth rate [-]": ("boi_growth_rate", float),
        "Boundary layer labels": ("boundary_layer_labels", list),
        "Boundary layer offset method": ("bl_offset_method", str),
        "Boundary layer first height factor [-]": (
            "bl_height_factor",
            float,
        ),
        "Boundary layer first height [mm]": ("bl_height", float),
        "Boundary layer number of layers [-]": ("bl_layers", int),
        "Boundary layer growth rate [-]": ("bl_growth_rate", float),
        "Volume hex max factor [-]": ("vol_hex_max_factor", float),
        "Volume hex max cell length [mm]": ("vol_hex_max", float),
        "Peel layers [-]": ("peel_layers", int),
        "Minimum orthogonal quality threshold [-]": (
            "min_orthogonal_quality_threshold",
            float,
        ),
        "Maximum aspect ratio threshold [-]": (
            "max_aspect_ratio_threshold",
            float,
        ),
    }
    parameters = {}
    for label, (name, value_type) in label_types.items():
        parameters[name] = _parse_scalar(
            _last_line_value(text, label),
            value_type,
        )

    periodic_matches = re.findall(
        r"Periodic translation \[mm\]:\s*"
        rf"dx={_FLOAT_PATTERN},\s*"
        rf"dy={_FLOAT_PATTERN},\s*"
        rf"dz={_FLOAT_PATTERN}",
        text,
        re.IGNORECASE,
    )
    if periodic_matches:
        x_value, y_value, z_value = periodic_matches[-1]
        parameters.update({
            "periodic_shift_x": float(x_value),
            "periodic_shift_y": float(y_value),
            "periodic_shift_z": float(z_value),
        })

    return parameters


def mesh_parameters_from_mapping(values):
    """Collect every mesh parameter used by the worker from a mapping."""
    parameters = {
        name: values.get(name)
        for name in MESH_PARAMETER_NAMES
    }
    if parameters["boundary_layer_labels"] is None:
        labels = []
        for key in (
            "active_membrane_wall_labels",
            "buffer_wall_labels",
            "wall_spacer_labels",
        ):
            labels.extend(parameters.get(key) or [])
        parameters["boundary_layer_labels"] = labels or None
    if parameters["bl_height"] is None:
        m_min = parameters.get("m_min")
        factor = parameters.get("bl_height_factor")
        if m_min is not None and factor is not None:
            parameters["bl_height"] = m_min * factor
    if parameters["vol_hex_max"] is None:
        m_max = parameters.get("m_max")
        factor = parameters.get("vol_hex_max_factor")
        if m_max is not None and factor is not None:
            parameters["vol_hex_max"] = m_max * factor
    return parameters


def evaluate_quality_gate(mesh_parameters, metrics):
    """Return True/False when parsed values can determine the configured gate."""
    min_quality = metrics.get("min_orthogonal_quality")
    max_aspect = metrics.get("max_aspect_ratio")
    min_limit = mesh_parameters.get("min_orthogonal_quality_threshold")
    max_limit = mesh_parameters.get("max_aspect_ratio_threshold")
    if min_quality is not None and min_limit is not None:
        if min_quality < min_limit:
            return False
    elif mesh_parameters.get("fail_if_quality_not_parsed"):
        return False
    if max_limit is not None:
        if max_aspect is not None:
            if max_aspect > max_limit:
                return False
        elif mesh_parameters.get("fail_if_quality_not_parsed"):
            return False
    if min_quality is None and max_aspect is None:
        return None
    return True


def build_mesh_ledger_record(
    *,
    geo_name,
    mesh_case_name,
    mesh_parameters,
    status,
    exit_code,
    wall_time_seconds,
    metrics,
    mesh_log_path,
    mesh_file_path,
    error_summary="",
    recorded_at_utc=None,
):
    """Build one stable-schema mesh ledger record."""
    record = {name: None for name in MESH_LEDGER_FIELDNAMES}
    record.update({
        "geo_name": geo_name,
        "mesh_case_name": mesh_case_name,
        "status": status,
        "exit_code": exit_code,
        "wall_time_seconds": wall_time_seconds,
        "mesh_log_path": str(mesh_log_path),
        "mesh_file_path": str(mesh_file_path),
        "error_summary": error_summary,
        "recorded_at_utc": recorded_at_utc or datetime.now(
            timezone.utc
        ).isoformat(),
    })
    for name in MESH_PARAMETER_NAMES:
        record[name] = mesh_parameters.get(name)
    for name in MESH_METRIC_NAMES:
        record[name] = metrics.get(name)
    record["quality_gate_passed"] = evaluate_quality_gate(
        mesh_parameters,
        metrics,
    )
    (
        record["canonical_mesh_case_name"],
        record["mesh_case_name_validation"],
    ) = mesh_case_name_provenance(mesh_case_name, mesh_parameters)
    return record


def write_mesh_run_record(path, record):
    """Atomically write one per-case JSON record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def load_mesh_run_record(path):
    """Load one worker JSON record, returning None when unavailable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _csv_value(value):
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def upsert_mesh_ledger_csv(path, records):
    """Atomically upsert records by geometry and mesh case."""
    path = Path(path)
    existing = {}
    if path.is_file():
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                existing[(row.get("geo_name"), row.get("mesh_case_name"))] = row

    for record in records:
        key = (record.get("geo_name"), record.get("mesh_case_name"))
        existing[key] = {
            name: _csv_value(record.get(name))
            for name in MESH_LEDGER_FIELDNAMES
        }

    ordered_rows = [
        existing[key]
        for key in sorted(existing)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=MESH_LEDGER_FIELDNAMES,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(ordered_rows)
    os.replace(temp_path, path)
