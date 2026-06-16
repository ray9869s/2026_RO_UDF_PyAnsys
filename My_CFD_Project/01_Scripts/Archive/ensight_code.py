# %%
# ==========================================================
# RO CFD PyEnSight Post-processing: CP Modulus Contour Only
# ==========================================================
# Purpose:
#   - Load Fluent .cas.h5/.dat.h5 result in EnSight.
#   - Detect fluid and membrane wall parts automatically.
#   - Create CP modulus variable:
#       CP = Mass_fraction_of_nacl / C_bulk
#   - Save membrane-wall top-view CP modulus contour.
#   - Export a small CP-only summary JSON/CSV.
#
# Notes:
#   - Pressure drop, LMH, velocity/pathline, and wall shear results are intended
#     to be obtained directly from Fluent.
#   - This script is only for CP contour automation.
#   - For final reported CP average, use Fluent reports if desired:
#       CP_avg = wall area-weighted avg NaCl / fluid volume-weighted avg NaCl
# ==========================================================

# %%
# ==========================================================
# [0] Import Packages
# ==========================================================

import os
import re
import json
import glob
import shutil

import numpy as np
import pandas as pd

from ansys.pyensight.core import LocalLauncher


# %%
# ==========================================================
# [1] User Inputs
# ==========================================================

project_root = "C:/PyFluent/My_CFD_Project"
geo_name = "===== Edit here ====="
case_name = "===== Edit here ====="

# CP bulk reference.
#   1) In Fluent, create the mid-channel bulk plane between membranes.
#   2) Calculate Area-Weighted Average of Mass fraction of nacl on that plane.
#   3) Set use_manual_bulk_mass_fraction_for_cp = True and paste that value below.
use_manual_bulk_mass_fraction_for_cp = True
manual_bulk_mass_fraction_for_cp = 0.035

case_path = os.path.join(project_root, "03_Results", geo_name, case_name)

case_file = os.path.join(case_path, f"{geo_name}_{case_name}_final.cas.h5")
data_file = os.path.join(case_path, f"{geo_name}_{case_name}_final.dat.h5")

solver_mesh_replace_log_path = os.path.join(
    case_path,
    f"solver_mesh_replace_log_{case_name}.txt",
)

post_path = os.path.join(case_path, "ensight_post")
figure_path = os.path.join(post_path, "figures")
table_path = os.path.join(post_path, "tables")

os.makedirs(figure_path, exist_ok=True)
os.makedirs(table_path, exist_ok=True)

# Coordinate convention:
# x = flow direction, y = channel width direction, z = vertical direction.

# Part-name rules.
# base.N matching is supported. Example: wall, wall.1, wall.2.
fluid_base_names = ["solid", "fluid", "domain"]
membrane_wall_base_names = ["wall"]
membrane_wall_exclude_base_names = ["wall_spacer", "spacer"]

use_auto_domain_extents = True
allow_manual_domain_extent_fallback = False

manual_extents = {
    "x_min": 0.0,
    "x_max": 0.010395,
    "y_min": 0.0,
    "y_max": 0.003465,
    "z_min": 0.0,
    "z_max": 0.000770,
}

remote_width = 1400
remote_height = 900
image_width = 1600
image_height = 1000

cp_view_scale = 1.5

# Close EnSight after saving by default.
close_session_at_end = True

# CP color range for all cases.
# The CP contour image is saved WITHOUT a visible colorbar/legend, but these
# min/max values still control the color mapping. Use the same values when
# making a separate colorbar for the poster.
cp_contour_min = 0.95
cp_contour_max = 1.50

contour_ranges = {
    "cp": {"min": cp_contour_min, "max": cp_contour_max},
}

for path, label in [
    (case_file, "case file"),
    (data_file, "data file"),
]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing {label}: {path}")

if use_auto_domain_extents and not os.path.isfile(solver_mesh_replace_log_path):
    if not allow_manual_domain_extent_fallback:
        raise FileNotFoundError(
            f"Missing mesh-replace log: {solver_mesh_replace_log_path}"
        )

print("Case file:", case_file)
print("Data file:", data_file)
print("Post path:", post_path)


# %%
# ==========================================================
# [2] Basic Helpers
# ==========================================================

def ens_path(path):
    """Return an EnSight-compatible absolute path."""
    return os.path.abspath(path).replace("\\", "/")


def as_list(obj):
    """Return an object as a Python list."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    try:
        return list(obj)
    except Exception:
        return [obj]


def safe_attr(obj, name, default=None):
    """Read an attribute safely."""
    try:
        value = getattr(obj, name)
        if isinstance(value, list) and len(value) == 1:
            return value[0]
        return value
    except Exception:
        return default


def ens_name(obj):
    """Return a readable EnSight object name."""
    if obj is None:
        return "None"
    for key in ["DESCRIPTION", "NAME", "PARTNUMBER", "ID"]:
        value = safe_attr(obj, key, None)
        if value is not None:
            return str(value)
    return str(obj)


def print_section(title):
    """Print a section title."""
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def parse_float_or_none(value):
    """Convert a user range value to float or None."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return float(value)


def get_range(range_key):
    """Return a contour range dictionary as numeric values."""
    if range_key is None:
        return None
    cfg = contour_ranges.get(range_key)
    if cfg is None:
        return None
    vmin = parse_float_or_none(cfg.get("min"))
    vmax = parse_float_or_none(cfg.get("max"))
    if vmin is None and vmax is None:
        return None
    return {"min": vmin, "max": vmax}


def parse_domain_extents(log_path):
    """Parse domain extents from Fluent /mesh/check output."""
    if not os.path.isfile(log_path):
        return dict(manual_extents)

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    out = {}

    for axis in ["x", "y", "z"]:
        pattern = (
            rf"{axis}-coordinate:\s*min\s*\(m\)\s*=\s*{number},"
            rf"\s*max\s*\(m\)\s*=\s*{number}"
        )
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if not matches:
            raise RuntimeError(f"Could not parse {axis}-extent from {log_path}")
        out[f"{axis}_min"], out[f"{axis}_max"] = map(float, matches[-1])

    return out


def validate_extents(ext):
    """Validate domain extents."""
    for axis in ["x", "y", "z"]:
        if ext[f"{axis}_max"] <= ext[f"{axis}_min"]:
            raise ValueError(f"Invalid {axis}-extent: {ext}")


# %%
# ==========================================================
# [3] Domain Extents
# ==========================================================

if use_auto_domain_extents:
    try:
        extents = parse_domain_extents(solver_mesh_replace_log_path)
    except Exception as e:
        if allow_manual_domain_extent_fallback:
            print("Using manual extents. Parse error:", e)
            extents = dict(manual_extents)
        else:
            raise
else:
    extents = dict(manual_extents)

validate_extents(extents)

x_min, x_max = extents["x_min"], extents["x_max"]
y_min, y_max = extents["y_min"], extents["y_max"]
z_min, z_max = extents["z_min"], extents["z_max"]

x_mid = 0.5 * (x_min + x_max)
y_mid = 0.5 * (y_min + y_max)
z_mid = 0.5 * (z_min + z_max)

x_length = x_max - x_min
y_length = y_max - y_min
z_length = z_max - z_min

print_section("Domain Extents")
for axis in ["x", "y", "z"]:
    print(
        f"{axis}: min={extents[f'{axis}_min']:.9e}, "
        f"max={extents[f'{axis}_max']:.9e}, "
        f"length={extents[f'{axis}_max'] - extents[f'{axis}_min']:.9e}"
    )


# %%
# ==========================================================
# [4] Launch EnSight and Load Data
# ==========================================================

session = LocalLauncher().start()

ensight = session.ensight
core = ensight.objs.core
enums = ensight.objs.enums
parts_util = ensight.utils.parts

session.load_data(
    data_file=ens_path(case_file),
    result_file=ens_path(data_file),
)

remote = session.show("remote", width=remote_width, height=remote_height)
try:
    remote.browser()
except Exception:
    pass

print("EnSight session started.")


# %%
# ==========================================================
# [5] Parts and Variables
# ==========================================================

def all_parts():
    """Return all model parts."""
    return as_list(core.PARTS)


def all_variables():
    """Return all variables."""
    return as_list(core.VARIABLES)


def match_base_name(name, base):
    """
    Match base or base.N only.

    Example for base='wall':
        match    : wall, wall.1, wall.2
        no match : wall_spacer, sidewall, wall-spacer
    """
    if name == base:
        return True
    if name.startswith(base + "."):
        suffix = name[len(base) + 1:]
        return suffix.isdigit()
    return False


def part_sort_key(part):
    """Sort parts by readable name and numeric suffix."""
    name = ens_name(part)
    m = re.match(r"^(.*)\.(\d+)$", name)
    if m:
        return (m.group(1), int(m.group(2)))
    return (name, -1)


def parts_by_bases(include_bases, exclude_bases=None):
    """Find parts matching include base names but not exclude base names."""
    if exclude_bases is None:
        exclude_bases = []

    out = []
    seen = set()

    for p in all_parts():
        name = ens_name(p)
        include = any(match_base_name(name, b) for b in include_bases)
        exclude = any(match_base_name(name, b) for b in exclude_bases)
        if include and not exclude and name not in seen:
            out.append(p)
            seen.add(name)

    return sorted(out, key=part_sort_key)


def variable_by_name(candidates, required=True):
    """Find a variable by exact or partial name."""
    vars_ = [(ens_name(v), v) for v in all_variables()]

    for c in candidates:
        for name, var in vars_:
            if name.lower() == c.lower():
                print(f"Variable selected: {name}")
                return var

    for c in candidates:
        for name, var in vars_:
            if c.lower() in name.lower():
                print(f"Variable selected: {name}")
                return var

    if required:
        names = [name for name, _ in vars_]
        raise RuntimeError(f"Variable not found: {candidates}\nAvailable: {names}")

    print(f"Variable not found: {candidates}")
    return None


try:
    fluid_dim_parts = as_list(parts_util.select_parts_by_dimension(3))
except Exception:
    fluid_dim_parts = []

fluid_parts = []
seen_fluid = set()
for p in fluid_dim_parts:
    name = ens_name(p)
    if name not in seen_fluid:
        fluid_parts.append(p)
        seen_fluid.add(name)

# Fallback if dimension-based selection is unavailable.
if not fluid_parts:
    fluid_parts = parts_by_bases(fluid_base_names)

membrane_parts = parts_by_bases(
    membrane_wall_base_names,
    exclude_bases=membrane_wall_exclude_base_names,
)

if not fluid_parts:
    raise RuntimeError("No fluid parts found. Check fluid_base_names or 3D part detection.")
if not membrane_parts:
    raise RuntimeError("No membrane wall parts found. Check membrane_wall_base_names.")

salt_var = variable_by_name([
    "Mass_fraction_of_nacl",
    "Mass Fraction of nacl",
    "Mass fraction of nacl",
    "nacl",
    "salt",
])

print_section("Parts")
print("Fluid:", [ens_name(p) for p in fluid_parts])
print("Membrane:", [ens_name(p) for p in membrane_parts])

print_section("Variables")
print("Salt:", ens_name(salt_var))


# %%
# ==========================================================
# [6] Data Extraction and Derived Variable
# ==========================================================

def create_var(name, expression, sources=None):
    """Create a scalar variable if it does not already exist."""
    existing = variable_by_name([name], required=False)
    if existing is not None:
        return existing

    try:
        if sources is None:
            var = core.create_variable(name, expression)
        else:
            var = core.create_variable(name, expression, sources=sources)
        print(f"Created variable: {name} = {expression}")
        return var
    except Exception as e:
        print(f"Could not create variable {name}: {e}")
        return None


def activate(var):
    """Activate a variable for extraction."""
    if var is None:
        return
    for fn in [
        lambda: setattr(var, "ACTIVE", True),
        lambda: setattr(var, "active", True),
        lambda: var.setattr("ACTIVE", True),
        lambda: ensight.variables.activate(ens_name(var)),
    ]:
        try:
            fn()
            return
        except Exception:
            pass


def scalar_values_from_raw(raw, var):
    """
    Extract only the requested scalar variable values from part.get_values() output.

    PyEnSight get_values() often returns:
        {
            "VariableName": array([...]),
            "ELEMENT_IDS": array([...])
        }

    This function intentionally ignores metadata arrays such as ELEMENT_IDS.
    """
    if raw is None or var is None:
        return np.array([], dtype=float)

    var_name = ens_name(var)

    if isinstance(raw, dict):
        if var_name in raw:
            arr = np.asarray(raw[var_name], dtype=float).ravel()
            return arr[np.isfinite(arr)]

        candidates = []
        for k, v in raw.items():
            if isinstance(k, str) and k.upper() in [
                "ELEMENT_IDS",
                "NODE_IDS",
                "PART_IDS",
            ]:
                continue
            try:
                arr = np.asarray(v, dtype=float).ravel()
            except Exception:
                continue
            if arr.size:
                candidates.append(arr[np.isfinite(arr)])

        if len(candidates) == 1:
            return candidates[0]

        print("Ambiguous get_values() output.")
        print("variable:", var_name)
        print("raw keys:", list(raw.keys()))
        return np.array([], dtype=float)

    try:
        arr = np.asarray(raw, dtype=float).ravel()
        return arr[np.isfinite(arr)]
    except Exception:
        return np.array([], dtype=float)


def part_values(part, var):
    """Extract scalar values from a part, excluding metadata such as ELEMENT_IDS."""
    if part is None or var is None:
        return np.array([], dtype=float)

    activate(var)

    try:
        part.VISIBLE = True
        part.COLORBYPALETTE = var
    except Exception:
        pass

    attempts = [
        lambda: part.get_values([ens_name(var)]),
        lambda: part.get_values((ens_name(var),)),
        lambda: part.get_values([var]),
        lambda: part.get_values((var,)),
    ]

    last_error = None
    for fn in attempts:
        try:
            raw = fn()
            vals = scalar_values_from_raw(raw, var)
            if vals.size:
                return vals
        except Exception as e:
            last_error = e

    print(
        f"Value extraction failed: part={ens_name(part)}, "
        f"var={ens_name(var)}, error={last_error}"
    )
    return np.array([], dtype=float)


def parts_values(parts, var):
    """Extract scalar values from multiple parts."""
    arrs = []
    for p in as_list(parts):
        vals = part_values(p, var)
        print(f"Extracted {vals.size} values: part={ens_name(p)}, var={ens_name(var)}")
        if vals.size:
            arrs.append(vals)
    return np.concatenate(arrs) if arrs else np.array([], dtype=float)


def stats(values, label, var_name):
    """Compute scalar statistics."""
    values = np.asarray(values, dtype=float).ravel()
    values = values[np.isfinite(values)]

    out = {
        "variable": var_name,
        "count": int(values.size),
        "min": None,
        "max": None,
        "mean": None,
        "std": None,
    }

    print_section(label)
    print("variable:", var_name)

    if values.size == 0:
        print("No valid values.")
        return out

    out["min"] = float(np.min(values))
    out["max"] = float(np.max(values))
    out["mean"] = float(np.mean(values))
    out["std"] = float(np.std(values))

    print(f"count : {out['count']}")
    print(f"min   : {out['min']:.9e}")
    print(f"max   : {out['max']:.9e}")
    print(f"mean  : {out['mean']:.9e}")
    print(f"std   : {out['std']:.9e}")
    return out


# Determine CP denominator.
if use_manual_bulk_mass_fraction_for_cp:
    if manual_bulk_mass_fraction_for_cp is None:
        raise ValueError(
            "use_manual_bulk_mass_fraction_for_cp=True requires "
            "manual_bulk_mass_fraction_for_cp."
        )
    bulk_mass_fraction = float(manual_bulk_mass_fraction_for_cp)
    bulk_mass_fraction_source = "manual_fluent_volume_weighted_average"
else:
    fluid_salt = parts_values(fluid_parts, salt_var)
    fluid_salt_stats = stats(
        fluid_salt,
        "Fluid NaCl Mass Fraction for CP Bulk Reference",
        ens_name(salt_var),
    )
    if fluid_salt_stats["mean"] is None:
        raise RuntimeError("Bulk concentration extraction failed.")
    bulk_mass_fraction = fluid_salt_stats["mean"]
    bulk_mass_fraction_source = "pyensight_arithmetic_mean"

print_section("CP Bulk Reference")
print("bulk_mass_fraction_source:", bulk_mass_fraction_source)
print("bulk_mass_fraction:", bulk_mass_fraction)

cp_var = create_var(
    "CP_modulus_auto",
    f"{ens_name(salt_var)}/{bulk_mass_fraction:.16e}",
    sources=fluid_parts + membrane_parts,
)

if cp_var is None:
    raise RuntimeError("Could not create CP_modulus_auto variable.")


# %%
# ==========================================================
# [7] Display and Image Helpers
# ==========================================================

def set_visible(parts, visible):
    """Set visibility for selected parts."""
    for p in as_list(parts):
        try:
            p.VISIBLE = visible
        except Exception:
            pass


def hide_all():
    """Hide all parts."""
    try:
        core.PARTS.set_attr("VISIBLE", False)
    except Exception:
        set_visible(all_parts(), False)


def apply_variable_range(var, range_key):
    """Apply a manual display range to a variable."""
    if var is None:
        return

    cfg = get_range(range_key)
    if cfg is None:
        return

    vmin = cfg["min"]
    vmax = cfg["max"]

    try:
        if vmin is not None and vmax is not None:
            var.MINMAX = [float(vmin), float(vmax)]
        elif vmin is not None:
            current = safe_attr(var, "MINMAX", None)
            if isinstance(current, list) and len(current) == 2:
                var.MINMAX = [float(vmin), current[1]]
        elif vmax is not None:
            current = safe_attr(var, "MINMAX", None)
            if isinstance(current, list) and len(current) == 2:
                var.MINMAX = [current[0], float(vmax)]
    except Exception:
        pass

    for attr, value in [
        ("OVERRIDERANGEMIN", 1.0 if vmin is not None else 0.0),
        ("OVERRIDERANGEMAX", 1.0 if vmax is not None else 0.0),
    ]:
        try:
            setattr(var, attr, value)
        except Exception:
            pass

    print(f"Applied display range to {ens_name(var)}: min={vmin}, max={vmax}")


def show_scene(parts, var=None, title=None, range_key=None):
    """Show selected parts and optionally color by a variable."""
    if title:
        print_section(title)

    parts = as_list(parts)
    hide_all()

    for p in parts:
        try:
            p.VISIBLE = True
            p.OPAQUENESS = 1.0
        except Exception:
            pass

    if var is not None:
        activate(var)
        apply_variable_range(var, range_key)
        for p in parts:
            try:
                p.COLORBYPALETTE = var
            except Exception:
                pass

    try:
        ensight.view_transf.fit(0)
    except Exception:
        pass

    try:
        remote.update()
    except Exception:
        pass

    print("Visible:", [ens_name(p) for p in parts])
    print("Color:", ens_name(var) if var is not None else None)


view_settings = {
    "top_xy": {
        "rotate_sequence": [(0.0, 0.0, 0.0)],
    },
}


def set_view(view):
    """Set saved-image view using initialize -> fit -> rotate -> fit."""
    if view not in view_settings:
        raise ValueError("view must be top_xy for this CP-only script.")

    cfg = view_settings[view]

    try:
        ensight.view_transf.initialize_viewports()
    except Exception:
        pass
    try:
        ensight.view_transf.fit(0)
    except Exception:
        pass
    try:
        for rx, ry, rz in cfg["rotate_sequence"]:
            ensight.view_transf.rotate(float(rx), float(ry), float(rz))
    except Exception:
        pass
    try:
        ensight.view_transf.fit(0)
    except Exception:
        pass

    try:
        ensight.view_transf.zoom(float(cp_view_scale))
    except Exception as e:
        print("View scale failed:", e)

    try:
        remote.update()
    except Exception:
        pass


def clean_scene_background():
    """Turn off common EnSight visual aids. Commands are version-guarded."""
    commands = [
        "ensight.annotation.axis_model('OFF')",
        "ensight.annotation.axis_view('OFF')",
        "ensight.annotation.logo('OFF')",
        "ensight.annotation.legend('OFF')",
        "ensight.view.grid('OFF')",
        "ensight.view.bounds('OFF')",
        "ensight.view.hidden_line('OFF')",
        "ensight.view.perspective('OFF')",
        "ensight.view.reflections('OFF')",
        "ensight.view.ground_plane('OFF')",
        "ensight.view.floor('OFF')",
        "ensight.view.background_type('SOLID')",
        "ensight.view.background_color(1.0, 1.0, 1.0)",
        "ensight.view.viewport.background_type('SOLID')",
        "ensight.view.viewport.background_color(1.0, 1.0, 1.0)",
    ]
    for cmd in commands:
        try:
            session.cmd(cmd)
        except Exception:
            pass


def hide_legend():
    """Hide colorbar/legend before saving image."""
    commands = [
        "ensight.annotation.legend('OFF')",
        "ensight.legend.visible('OFF')",
        "ensight.legend.hide()",
    ]

    for cmd in commands:
        try:
            session.cmd(cmd)
        except Exception:
            pass


def save_image(stem, view="top_xy"):
    """Save current scene as PNG."""
    set_view(view)
    clean_scene_background()
    hide_legend()

    for _ in range(3):
        try:
            remote.update()
        except Exception:
            pass

    target = os.path.join(figure_path, f"{case_name}_{stem}.png")
    tmp_dir = os.path.join(figure_path, f"_tmp_{stem}")

    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        img = session.show("image", width=image_width, height=image_height)
        img.download(tmp_dir)
        pngs = sorted(
            glob.glob(os.path.join(tmp_dir, "**", "*.png"), recursive=True),
            key=os.path.getmtime,
        )
        if not pngs:
            raise RuntimeError(f"No PNG downloaded to {tmp_dir}")
        if os.path.isfile(target):
            os.remove(target)
        shutil.move(pngs[-1], target)
    finally:
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

    print("Saved image:", target)
    return target


# %%
# ==========================================================
# [8] Optional Mid-plane Mask
# ==========================================================

def clip_default():
    """Return the default clip-plane part."""
    try:
        return core.DEFAULTPARTS[ensight.PART_CLIP_PLANE]
    except Exception:
        return session.cmd("ensight.objs.core.DEFAULTPARTS[ensight.PART_CLIP_PLANE]")


def first_part(created):
    """Return the first created part."""
    try:
        return created[0]
    except Exception:
        return created


def make_clip(name, axis, value, sources=None):
    """Create a coordinate-normal clip plane."""
    if sources is None:
        sources = fluid_parts

    axis = axis.lower()
    mesh_plane = {
        "x": enums.MESH_SLICE_X,
        "y": enums.MESH_SLICE_Y,
        "z": enums.MESH_SLICE_Z,
    }[axis]

    part = first_part(
        clip_default().createpart(
            name=name,
            sources=sources,
            attributes=[
                ["MESHPLANE", mesh_plane],
                ["TOOL", enums.CT_XYZ],
                ["VALUE", float(value)],
                ["DOMAIN", enums.CLIP_DOMAIN_INTER],
            ],
        )
    )
    print(f"Created clip: {name}, axis={axis}, value={value:.9e}")
    return part


# This mask is useful when top and bottom membrane walls are grouped together.
mask_part = make_clip("mask_xy_mid_between_membranes", "z", z_mid)
try:
    mask_part.COLORBYPALETTE = None
    mask_part.COLOR = [0.75, 0.75, 0.75]
    mask_part.OPAQUENESS = 1.0
    mask_part.VISIBLE = False
except Exception:
    pass


# %%
# ==========================================================
# [9] CP Modulus Contour and Summary
# ==========================================================

cp_values = parts_values(membrane_parts, cp_var)
cp_stats = stats(
    cp_values,
    "Membrane CP Modulus",
    ens_name(cp_var),
)

cp_summary = {
    "geo_name": geo_name,
    "case_name": case_name,
    "case_file": case_file,
    "data_file": data_file,
    "x_length_m": x_length,
    "y_length_m": y_length,
    "z_length_m": z_length,
    "fluid_parts": ";".join([ens_name(p) for p in fluid_parts]),
    "membrane_parts": ";".join([ens_name(p) for p in membrane_parts]),
    "salt_variable": ens_name(salt_var),
    "cp_variable": ens_name(cp_var),
    "bulk_mass_fraction_source": bulk_mass_fraction_source,
    "bulk_mass_fraction": bulk_mass_fraction,
    "cp_min_pyensight": cp_stats["min"],
    "cp_max_pyensight": cp_stats["max"],
    "cp_avg_pyensight": cp_stats["mean"],
    "cp_std_pyensight": cp_stats["std"],
    "cp_count_pyensight": cp_stats["count"],
    "note": (
        "PyEnSight CP statistics are arithmetic means over extracted membrane values. "
        "For final reported CP_avg, use Fluent wall area-weighted average NaCl "
        "divided by Fluent mid-channel bulk-plane area-weighted average NaCl."
    ),
}

show_scene(
    [mask_part] + membrane_parts,
    cp_var,
    "Membrane CP modulus",
    range_key="cp",
)

try:
    mask_part.COLORBYPALETTE = None
    mask_part.COLOR = [0.75, 0.75, 0.75]
    mask_part.OPAQUENESS = 1.0
except Exception:
    pass
for p in membrane_parts:
    try:
        p.COLORBYPALETTE = cp_var
    except Exception:
        pass

cp_image = save_image("membrane_cp_top_xy", "top_xy")
cp_summary["cp_image"] = cp_image

print_section("Final CP-only Summary")
for k, v in cp_summary.items():
    print(f"{k}: {v}")

json_path = os.path.join(table_path, f"{case_name}_cp_only_summary.json")
csv_path = os.path.join(table_path, f"{case_name}_cp_only_summary.csv")

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(cp_summary, f, indent=2)

pd.DataFrame([cp_summary]).to_csv(csv_path, index=False)

print("Saved JSON:", json_path)
print("Saved CSV:", csv_path)


# %%
# ==========================================================
# [10] Cleanup
# ==========================================================

if close_session_at_end:
    try:
        session.close()
        print("EnSight session closed.")
    except Exception as e:
        print("Could not close EnSight session:", e)
else:
    print("EnSight session remains open.")
