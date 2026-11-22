"""One-off: compare c_b definitions on x-normal planes of one run leaf.

Not part of any queue (no ``--emit-queue``). Run by hand while the host
queue is paused. For one leaf it:

* copies the run leaf and its mesh manifest under ``--data-root`` (the
  ``locate_mass_imbalance`` copy: production leaves are only read, nothing
  is written under ``C:/ro_data``);
* takes the host lock (``ro.host_lock``) and refuses while Fluent runs;
* opens the copied final case+data read-only (no iteration, no write);
* prints the surface report types Fluent allows, then on every unit-cell
  boundary plane and on one mid-cell x-normal plane per cell evaluates
  - ``surface-massavg`` of the salt mass fraction (|rho u.n| weight),
  - ``surface-flowrate`` of it over ``surface-massflowrate`` (net flux),
  - the same net ratio from ``solver.fields.reduction``
    (``mass_flow_integral`` / ``mass_flow_average``), an independent route,
  - ``surface-areaavg`` and plane area;
* writes ``planes.csv`` (per plane), ``cells.csv`` (per cell: boundary mean
  vs mid-cell value, massavg and net) and ``meta.json`` under
  ``<data-root>/reports/<geo>/<mesh>/<run>/c_b_definitions_<UTC>/``.

Each value is evaluated on its own; a failing report type or reduction
leaves that column blank with the error in the row, so one run shows which
routes work in Fluent 25.1. ``--dry-run`` prints the planes and writes
nothing (no copy, no Fluent).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

_MFBO = Path(__file__).resolve().parent
_SRC = _MFBO.parents[1] / "src"
for _path in (_SRC, _MFBO):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import locate_mass_imbalance as locate  # noqa: E402
from ro import bulk_cup  # noqa: E402
from ro.domain_layout import layout_from_mesh_manifest  # noqa: E402
from ro.fluent_launch import fluent_mpi_argument  # noqa: E402
from ro.paths import project_root, using_data_root  # noqa: E402

DATA_ROOT = "C:/ro_data_mfbo/c_b_definitions"
SALT_FIELD = "nacl"
SALT_EXPRESSION = f'MassFraction(species="{SALT_FIELD}")'
REPORT_TYPES = {
    "massavg": "surface-massavg",
    "areaavg": "surface-areaavg",
    "flowrate": bulk_cup.SURFACE_FLOW_RATE,
    "massflowrate": bulk_cup.SURFACE_MASS_FLOW_RATE,
    "area": "surface-area",
}
PLANE_FIELDS = (
    "kind", "index", "x_m", "area_m2",
    "y_massavg", "y_areaavg", "salt_flowrate_kg_s", "massflowrate_kg_s", "y_net_report",
    "reduction_salt_flow_kg_s", "reduction_mass_flow_kg_s", "y_net_reduction",
    "c_massavg_mol_m3", "c_net_mol_m3", "net_minus_massavg_rel", "errors",
)
CELL_FIELDS = (
    "cell", "boundary_mean_massavg_mol_m3", "midcell_massavg_mol_m3", "massavg_boundary_minus_mid_rel",
    "boundary_mean_net_mol_m3", "midcell_net_mol_m3", "net_boundary_minus_mid_rel",
    "midcell_net_minus_massavg_rel",
)


def plane_plan(boundary_x_m):
    """``[(kind, index, x)]``: every boundary k, then each cell's mid-plane."""
    xs = [float(x) for x in boundary_x_m]
    if len(xs) < 2 or any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError(f"boundary positions must increase, got {xs!r}.")
    plan = [("boundary", k, x) for k, x in enumerate(xs)]
    plan += [("midcell", n, 0.5 * (xs[n - 1] + xs[n])) for n in range(1, len(xs))]
    return plan


def plane_name(kind, index):
    return f"pp_cbdiag_{kind}_{int(index)}"


def _ratio(salt, mass):
    try:
        return bulk_cup.net_flux_cup_mass_fraction(salt, mass)
    except bulk_cup.CupError:
        return None


def _rel(a, b):
    if a is None or b is None or b == 0.0:
        return None
    return (a - b) / b


def plane_row(kind, index, x, values, errors, *, factor):
    """One ``planes.csv`` row from raw values (mass fractions, kg/s)."""
    y_net_report = _ratio(values.get("flowrate"), values.get("massflowrate"))
    y_net_reduction = _ratio(values.get("reduction_salt_flow"), values.get("reduction_mass_flow"))
    y_net = y_net_report if y_net_report is not None else y_net_reduction
    massavg = values.get("massavg")
    c_mass = None if massavg is None else float(massavg) * factor
    c_net = None if y_net is None else y_net * factor
    return {
        "kind": kind, "index": index, "x_m": x, "area_m2": values.get("area"),
        "y_massavg": massavg, "y_areaavg": values.get("areaavg"),
        "salt_flowrate_kg_s": values.get("flowrate"),
        "massflowrate_kg_s": values.get("massflowrate"),
        "y_net_report": y_net_report,
        "reduction_salt_flow_kg_s": values.get("reduction_salt_flow"),
        "reduction_mass_flow_kg_s": values.get("reduction_mass_flow"),
        "y_net_reduction": y_net_reduction,
        "c_massavg_mol_m3": c_mass, "c_net_mol_m3": c_net,
        "net_minus_massavg_rel": _rel(c_net, c_mass),
        "errors": "; ".join(errors),
    }


def cell_rows(plane_rows):
    """Per cell: mean of its two boundary values vs its mid-cell value."""
    boundary = {r["index"]: r for r in plane_rows if r["kind"] == "boundary"}
    rows = []
    for mid in (r for r in plane_rows if r["kind"] == "midcell"):
        n = mid["index"]
        left, right = boundary.get(n - 1), boundary.get(n)

        def mean(key):
            if left is None or right is None or left[key] is None or right[key] is None:
                return None
            return 0.5 * (left[key] + right[key])

        b_mass, b_net = mean("c_massavg_mol_m3"), mean("c_net_mol_m3")
        rows.append({
            "cell": n,
            "boundary_mean_massavg_mol_m3": b_mass,
            "midcell_massavg_mol_m3": mid["c_massavg_mol_m3"],
            "massavg_boundary_minus_mid_rel": _rel(b_mass, mid["c_massavg_mol_m3"]),
            "boundary_mean_net_mol_m3": b_net,
            "midcell_net_mol_m3": mid["c_net_mol_m3"],
            "net_boundary_minus_mid_rel": _rel(b_net, mid["c_net_mol_m3"]),
            "midcell_net_minus_massavg_rel": mid["net_minus_massavg_rel"],
        })
    return rows


def find_first_number(obj):
    """First number in a PyFluent compute result (as the extract reads it)."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)
    items = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, (list, tuple)) else ()
    for value in items:
        found = find_first_number(value)
        if found is not None:
            return found
    return None


def _post_config():
    path = project_root() / "configs" / "post_config.py"
    spec = importlib.util.spec_from_file_location("c_b_diag_post_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in fields})


def _allowed_report_types(solution):
    """Fluent's allowed ``report_type`` values (printed; None if unreadable)."""
    group = solution.report_definitions.surface
    name = "pp_cbdiag_type_probe"
    try:
        group.create(name)
    except Exception:
        pass
    try:
        return list(group[name].report_type.allowed_values())
    except Exception as exc:
        print(f"Could not read allowed report types: {exc}")
        return None


def _surface_report(solution, name, report_type, field, plane):
    from ro.fluent_report_helpers import apply_surface_report_definition

    group = solution.report_definitions.surface
    try:
        group.create(name)
    except Exception:
        pass
    apply_surface_report_definition(group[name], report_type, field, [plane])
    result = solution.report_definitions.compute(report_defs=[name])
    value = find_first_number(result)
    if value is None:
        raise RuntimeError(f"no number in {result!r}")
    return float(value)


def evaluate_plane(solver, kind, index, x, *, allowed):
    """Raw values on one new x-normal plane; each route fails on its own."""
    from ro.fluent_report_helpers import create_x_normal_plane

    name = plane_name(kind, index)
    create_x_normal_plane(solver, name, x)
    solution = solver.settings.solution
    values, errors = {}, []
    for key, report_type in REPORT_TYPES.items():
        if allowed is not None and report_type not in allowed:
            errors.append(f"{key}: {report_type} not an allowed report type")
            continue
        field = None if key in ("massflowrate", "area") else SALT_FIELD
        try:
            values[key] = _surface_report(solution, f"{name}_{key}", report_type, field, name)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    reduction = solver.fields.reduction
    try:
        location = [solver.settings.results.surfaces.iso_surface[name]]
        values["reduction_salt_flow"] = float(
            reduction.mass_flow_integral(expression=SALT_EXPRESSION, locations=location))
        values["reduction_mass_flow"] = float(
            reduction.mass_flow_integral(expression="1", locations=location))
    except Exception as exc:
        errors.append(f"reduction: {exc}")
    return values, errors


def run(prepared, *, rho, molecular_weight, x0):
    import ansys.fluent.core as pyfluent

    from ro.host_lock import host_lock_path, wait_for_no_fluent
    import job_queue

    run_config = locate._load_run_config()
    if run_config.product_version.split(".")[0:2] != ["25", "1"]:
        raise RuntimeError(f"checked for Fluent 25.1, got {run_config.product_version!r}.")
    with using_data_root(prepared["data_root"]):
        record = layout_from_mesh_manifest(Path(prepared["mesh_manifest"]).parent)
    plan = plane_plan(record.layout.boundary_positions(x0))
    found = wait_for_no_fluent()
    if found:
        raise RuntimeError("live Fluent process(es); refusing: " + "; ".join(found))
    lock = host_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    job_queue._acquire_lock(lock)
    meshing = solver = None
    try:
        kwargs = locate._launch_kwargs(prepared["run_leaf"], run_config)
        kwargs["additional_arguments"] = fluent_mpi_argument(
            getattr(run_config, "fluent_mpi", None))
        pyfluent.config.check_health_timeout = run_config.fluent_health_timeout
        meshing = pyfluent.launch_fluent(**kwargs)
        solver = meshing.switch_to_solver()
        meshing = None
        solver.settings.file.read_case_data(
            file_name=os.path.abspath(prepared["case_file"]).replace("\\", "/"))
        allowed = _allowed_report_types(solver.settings.solution)
        print(f"Allowed surface report types: {allowed}")
        factor = float(rho) / float(molecular_weight)
        planes = []
        for kind, index, x in plan:
            values, errors = evaluate_plane(solver, kind, index, x, allowed=allowed)
            row = plane_row(kind, index, x, values, errors, factor=factor)
            print(row)
            planes.append(row)
    finally:
        if meshing is not None:
            meshing.exit()
        if solver is not None:
            solver.exit()
        job_queue._release_lock(lock)
    identity = prepared["identity"]
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (Path(prepared["data_root"]) / "reports" / identity["geo_id"] / identity["mesh_id"]
           / identity["run_id"] / f"c_b_definitions_{stamp}")
    out.mkdir(parents=True, exist_ok=False)
    _write_csv(out / "planes.csv", PLANE_FIELDS, planes)
    cells = cell_rows(planes)
    _write_csv(out / "cells.csv", CELL_FIELDS, cells)
    (out / "meta.json").write_text(json.dumps({
        "source": identity["run_leaf"], "copy": str(prepared["run_leaf"]),
        "allowed_report_types": allowed, "rho": rho, "molecular_weight": molecular_weight,
        "x0": x0, "salt_field": SALT_FIELD,
    }, indent=2), encoding="utf-8")
    for row in cells:
        print(row)
    print(f"Wrote {out}")
    return out


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="Run leaf (production leaves are copied).")
    parser.add_argument("--data-root", default=DATA_ROOT, help="Copy and report root; refuses C:/ro_data.")
    parser.add_argument("--dry-run", action="store_true", help="Print the planes; no copy, no Fluent.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = locate.resolve_data_root(args.data_root)
    identity = locate.parse_run_leaf(args.source)
    cfg = _post_config()
    rho = float(cfg.rho)
    molecular_weight = float(getattr(cfg, "salt_molecular_weight_kg_per_mol", 0.05844))
    x0 = float(getattr(cfg, "domain_x_min_m", 0.0))
    if not all(math.isfinite(v) and v > 0 for v in (rho, molecular_weight)):
        raise ValueError("post_config rho and salt molecular weight must be positive.")
    if args.dry_run:
        mesh_dir = Path(identity["mesh_leaf"])
        with using_data_root(identity["root"]):
            record = layout_from_mesh_manifest(mesh_dir)
        for kind, index, x in plane_plan(record.layout.boundary_positions(x0)):
            print(f"{plane_name(kind, index)}: x = {x:.6e} m")
        print(f"DRY RUN: copy root {root}; nothing copied, Fluent not started.")
        return 0
    prepared = locate.prepare_copy(args.source, root)
    run(prepared, rho=rho, molecular_weight=molecular_weight, x0=x0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
