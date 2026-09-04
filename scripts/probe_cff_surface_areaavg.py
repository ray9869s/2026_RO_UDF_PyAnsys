#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only: can a Custom Field Function be used as a surface-areaavg field?

Does NOT modify the production CP path. Settles whether CFF appears in the
Fluent field tree for surface reports (contour success is not proof — named
expressions failed that same API).

Steps on one case:
  1. list_valid_cell_function_names → UDM token spelling
  2. Define a trivial CFF: <udm_token> / 1.0  (hyphen-safe token)
  3. surface-areaavg(field=CFF) on one eval-cell membrane x-clip
  4. Compare to surface-areaavg of the same UDM on that clip
  5. If (3) works: nested canon CFF with B and literal c_b; compare to
     film-theory + k_N from the same segment averages / published cp_canon

Usage::

    export RO_DATA_ROOT='C:/ro_data'
    python scripts/probe_cff_surface_areaavg.py \\
      --family diamond --geo-id D2450_a45 \\
      --mesh-id max085_min006_cpg5_bl4_peel2 --run-id u0p2_p6M
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from ro.cp_metrics import canonical_rescale_factor, film_theory_cp_perm_mol_m3
from ro.domain_layout import layout_from_run_directory
from ro.fluent_report_helpers import (
    compute_surface_report_value,
    create_or_update_surface_field_report,
    create_x_range_iso_clip,
    delete_iso_clip,
    delete_surface_field_report,
    list_named_object_names,
)
from ro.manifest import read_run_manifest
from ro.paths import data_root
from ro.solver_common import path_to_fluent_str
from ro.udm_layout import FIELD_UDM_CM, FIELD_UDM_JW

B_PERM_DEFAULT = 2.50e-8
TRIVIAL_CFF_NAME = "pp_probe_cff_udm_scale"
CANON_CFF_NAME = "pp_probe_cff_cp_canon"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probe CFF as surface-areaavg field (read-only)."
    )
    p.add_argument("--family", default="diamond")
    p.add_argument("--geo-id", default="D2450_a45")
    p.add_argument("--mesh-id", default="max085_min006_cpg5_bl4_peel2")
    p.add_argument("--run-id", default="u0p2_p6M")
    p.add_argument("--results-root", default=None)
    p.add_argument("--cell", type=int, default=None, help="Eval cell (default: first)")
    p.add_argument("--b-perm", type=float, default=B_PERM_DEFAULT)
    p.add_argument("--c-inlet-ref", type=float, default=None)
    p.add_argument("--processor-count", type=int, default=1)
    p.add_argument("--product-version", default="25.1.0")
    p.add_argument("--graphics-driver", default="null")
    p.add_argument("--ui-mode", default="no_gui")
    p.add_argument("--output-json", default=None)
    p.add_argument(
        "--dry-run-paths",
        action="store_true",
        help="Resolve case path only; do not launch Fluent.",
    )
    return p.parse_args(argv)


def case_dir_for(args: argparse.Namespace) -> Path:
    root = Path(args.results_root) if args.results_root else data_root()
    runs = root / "runs" if (root / "runs").is_dir() else root
    return runs / args.family / args.geo_id / args.mesh_id / args.run_id


def collect_membrane_walls(setup) -> list[str]:
    walls: list[str] = []
    try:
        wall_group = getattr(setup.boundary_conditions, "wall", None)
        if wall_group is not None:
            names = list_named_object_names(
                wall_group, "setup.boundary_conditions.wall"
            )
            walls = [n for n in names if "mem" in n.lower()]
    except Exception:
        walls = []
    return sorted(walls) if walls else ["wall_top_mem", "wall_bottom_mem"]


def read_cell_cb_and_canon(case_dir: Path, cell: int) -> dict[str, Any]:
    wide = case_dir / "post" / "reports" / "summary_metrics_wide.csv"
    out: dict[str, Any] = {"csv": str(wide) if wide.is_file() else None}
    if not wide.is_file():
        return out
    with wide.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return out
    row = rows[0]
    for key, dest in (
        (f"pp_c_b_midplane_cell_{cell}_mol_m3", "c_b"),
        (f"pp_cp_canon_cell_{cell}", "cp_canon_cell"),
        ("cp_canon_window_avg", "cp_canon_window_avg"),
        (f"pp_cm_mol_m3_cell_{cell}", "cm_avg_csv"),
        (f"pp_jw_m_per_s_cell_{cell}", "jw_avg_csv"),
    ):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            out[dest] = float(raw)
        except ValueError:
            out[dest] = raw
    return out


def list_cff_cell_function_names(solver) -> tuple[list[str], str]:
    """Return (names, raw_text) from Fluent TUI listing."""
    raw = ""
    try:
        result = solver.tui.define.custom_field_functions.list_valid_cell_function_names()
        raw = "" if result is None else str(result)
    except Exception as exc:
        return [], f"list_valid_cell_function_names failed: {exc}"
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]*", raw)
    # Prefer unique order-preserving.
    seen: set[str] = set()
    names: list[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            names.append(tok)
    return names, raw


def pick_udm_token(names: list[str], prefer: str) -> Optional[str]:
    """Choose a CFF-safe UDM token; avoid hyphenated forms when underscore exists."""
    candidates = []
    digit = prefer.split("-")[-1] if "-" in prefer else prefer.replace("udm", "")
    patterns = (
        prefer,
        prefer.replace("-", "_"),
        f"udm_{digit}",
        f"udm-{digit}",
        f"UDM-{digit}",
        f"UDM_{digit}",
    )
    lower_map = {n.lower(): n for n in names}
    for pat in patterns:
        hit = lower_map.get(pat.lower())
        if hit is not None:
            candidates.append(hit)
    if not candidates:
        # Fuzzy: any name containing udm and the digit.
        for n in names:
            nl = n.lower()
            if "udm" in nl and digit in nl:
                candidates.append(n)
    if not candidates:
        return None
    # Prefer underscore over hyphen (CFF hyphen = subtraction risk).
    for c in candidates:
        if "-" not in c:
            return c
    return candidates[0]


def write_trivial_scm(path: Path, cff_name: str, udm_token: str) -> None:
    """Scheme CFF: udm_token / 1.0 — same shape as templates/cff_wall_shear_rate.scm."""
    # Quote the field load name; keep the display/syntax token without hyphens
    # when possible (caller should pass underscore form).
    display = f"{udm_token} / (1.0)"
    text = (
        "(custom-field-function/define\n"
        f" '(((name {cff_name})"
        f' (display "{display}")'
        f' (syntax-tree ("/" "{udm_token}" 1.0))'
        f' (code (field-/ (field-load "{udm_token}") 1.0)))\n'
        "   ))\n"
    )
    path.write_text(text, encoding="utf-8")


def write_canon_scm(
    path: Path,
    cff_name: str,
    cm_token: str,
    jw_token: str,
    b_perm: float,
    c_b: float,
) -> None:
    """Nested film-theory canon: (cm - cp) / (cb - cp), cp = B*cm/(jw+B)."""
    # Fluent CFF Scheme trees are nested lists. Build via a direct-define
    # expression string first when .scm nesting is awkward — try expression
    # form in the probe runner; this .scm uses a compact code form.
    expr = (
        f"(({cm_token}) - (({b_perm}) * ({cm_token}) / (({jw_token}) + ({b_perm}))))"
        f" / (({c_b}) - (({b_perm}) * ({cm_token}) / (({jw_token}) + ({b_perm}))))"
    )
    # Store expression as display; load path tries settings/TUI define with expr.
    text = (
        "(custom-field-function/define\n"
        f" '(((name {cff_name}) (display \"{expr}\") "
        f"(syntax-tree (\"/\" "
        f"(\"-\" \"{cm_token}\" "
        f"(\"/\" (\"*\" {b_perm} \"{cm_token}\") (\"+\" \"{jw_token}\" {b_perm}))) "
        f"(\"-\" {c_b} "
        f"(\"/\" (\"*\" {b_perm} \"{cm_token}\") (\"+\" \"{jw_token}\" {b_perm}\"))))) "
        f"(code (field-/ "
        f"(field-- (field-load \"{cm_token}\") "
        f"(field-/ (field-* {b_perm} (field-load \"{cm_token}\")) "
        f"(field-+ (field-load \"{jw_token}\") {b_perm}))) "
        f"(field-- {c_b} "
        f"(field-/ (field-* {b_perm} (field-load \"{cm_token}\")) "
        f"(field-+ (field-load \"{jw_token}\") {b_perm})))))))\n"
        "   ))\n"
    )
    path.write_text(text, encoding="utf-8")
    path.with_suffix(".expr.txt").write_text(expr + "\n", encoding="utf-8")


def try_load_cff(solver, cff_file: Path) -> tuple[bool, str]:
    cff_path = path_to_fluent_str(cff_file)
    attempts = [
        ("tui.define.custom_field_functions.read",
         lambda: solver.tui.define.custom_field_functions.read(cff_path)),
        ("tui.define.custom_field_functions.load",
         lambda: solver.tui.define.custom_field_functions.load(cff_path)),
        ("tui.file.read_custom_field_functions",
         lambda: solver.tui.file.read_custom_field_functions(cff_path)),
    ]
    errors: list[str] = []
    for label, fn in attempts:
        try:
            fn()
            return True, label
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    return False, " | ".join(errors)


def try_define_cff_expression(solver, cff_name: str, expression: str) -> tuple[bool, str]:
    """Best-effort direct define (settings API then TUI)."""
    errors: list[str] = []
    try:
        cff_settings = solver.settings.results.custom_field_functions
        try:
            cff_settings.delete(cff_name)
        except Exception:
            pass
        try:
            cff_settings.create(cff_name)
        except Exception as exc:
            errors.append(f"settings create: {exc}")
        else:
            target = cff_settings[cff_name]
            for attr in ("definition", "expression", "field_function"):
                try:
                    setattr(target, attr, expression)
                    return True, f"settings.{attr}"
                except Exception as exc:
                    errors.append(f"settings.{attr}: {exc}")
    except Exception as exc:
        errors.append(f"settings.custom_field_functions: {exc}")

    try:
        define = solver.tui.define.custom_field_functions
        for meth_name in ("define", "create", "add"):
            meth = getattr(define, meth_name, None)
            if meth is None:
                continue
            try:
                meth(cff_name, expression)
                return True, f"tui.{meth_name}"
            except Exception as exc:
                errors.append(f"tui.{meth_name}: {exc}")
                try:
                    meth(cff_name)
                    # Some TUI paths prompt for expression separately — skip.
                except Exception:
                    pass
    except Exception as exc:
        errors.append(f"tui.define: {exc}")
    return False, " | ".join(errors)


def surface_areaavg(solution, surfaces: list[str], field: str, tag: str) -> float:
    name = f"pp_cff_probe_avg_{tag}"
    create_or_update_surface_field_report(
        solution, name, "surface-areaavg", field, surfaces
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def rel_err(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1.0e-30)
    return abs(a - b) / scale


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    import ansys.fluent.core as pyfluent  # noqa: PLC0415

    case_dir = case_dir_for(args)
    cas = case_dir / f"{args.geo_id}_{args.run_id}_final.cas.h5"
    result: dict[str, Any] = {
        "case_dir": str(case_dir),
        "cas": str(cas),
        "steps": {},
    }
    if not cas.is_file():
        result["error"] = f"missing case/data: {cas}"
        return result

    layout_record, _ = layout_from_run_directory(case_dir)
    layout = layout_record.layout
    window = layout_record.evaluation_window
    eval_cells = window.evaluation_cell_numbers(layout)
    cell = int(args.cell) if args.cell is not None else int(eval_cells[0])
    if cell not in eval_cells:
        result["error"] = f"cell {cell} not in eval window {eval_cells}"
        return result
    result["cell"] = cell
    result["eval_cells"] = eval_cells

    run_manifest = read_run_manifest(case_dir)
    x0 = float(run_manifest.get("domain_extent_x_min_m") or 0.0)
    boundaries = layout.boundary_positions(x0)
    x_min = boundaries[cell - 1]
    x_max = boundaries[cell]
    b_perm = float(args.b_perm)
    c0 = float(
        args.c_inlet_ref
        if args.c_inlet_ref is not None
        else run_manifest.get("c_inlet_ref") or 597.8268309
    )
    csv_meta = read_cell_cb_and_canon(case_dir, cell)
    result["csv_meta"] = csv_meta

    print(f"Launching Fluent for {args.geo_id}/{args.run_id} cell {cell} ...")
    meshing = None
    solver = None
    tmp_dir = Path(tempfile.mkdtemp(prefix="cff_probe_"))
    try:
        meshing = pyfluent.launch_fluent(
            product_version=args.product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=args.processor_count,
            ui_mode=args.ui_mode,
            graphics_driver=args.graphics_driver,
            cwd=path_to_fluent_str(case_dir),
        )
        solver = meshing.switch_to_solver()
        meshing = None
        setup = solver.settings.setup
        solution = solver.settings.solution
        solver.settings.file.read_case_data(file_name=path_to_fluent_str(cas))
        walls = collect_membrane_walls(setup)
        result["walls"] = walls

        # ---- Step 1: UDM token spelling ----
        print("\n[1] list_valid_cell_function_names (UDM tokens)")
        names, raw = list_cff_cell_function_names(solver)
        udm_hits = [n for n in names if "udm" in n.lower()]
        cm_token = pick_udm_token(names, FIELD_UDM_CM)
        jw_token = pick_udm_token(names, FIELD_UDM_JW)
        step1 = {
            "pass": cm_token is not None and jw_token is not None,
            "udm_hits": udm_hits,
            "cm_token": cm_token,
            "jw_token": jw_token,
            "raw_head": raw[:800],
        }
        result["steps"]["1_udm_tokens"] = step1
        print(f"  UDM-related tokens ({len(udm_hits)}): {udm_hits[:40]}")
        print(f"  cm_token={cm_token!r} jw_token={jw_token!r}")
        if not step1["pass"]:
            print("  FAIL: could not resolve UDM tokens for CFF")
            return result
        print("  PASS")

        # ---- Step 2: define trivial CFF ----
        print("\n[2] Define trivial CFF (udm / 1.0)")
        scm = tmp_dir / f"{TRIVIAL_CFF_NAME}.scm"
        write_trivial_scm(scm, TRIVIAL_CFF_NAME, cm_token)
        ok_load, how = try_load_cff(solver, scm)
        if not ok_load:
            ok_load, how = try_define_cff_expression(
                solver, TRIVIAL_CFF_NAME, f"{cm_token} / 1.0"
            )
        step2 = {"pass": ok_load, "method": how, "scm": str(scm)}
        result["steps"]["2_define_trivial"] = step2
        print(f"  method={how}")
        if not ok_load:
            print("  FAIL: could not define CFF")
            return result
        print("  PASS")

        # ---- Step 3: surface-areaavg(CFF) ----
        print("\n[3] surface-areaavg(field=CFF) on eval-cell membrane clip")
        clip = f"pp_cff_probe_x_c{cell}"
        create_x_range_iso_clip(solver, clip, walls, x_min, x_max)
        try:
            try:
                cff_avg = surface_areaavg(
                    solution, [clip], TRIVIAL_CFF_NAME, f"cff_c{cell}"
                )
                step3 = {"pass": True, "cff_avg": cff_avg}
            except Exception as exc:
                step3 = {
                    "pass": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                result["steps"]["3_areaavg_cff"] = step3
                print(f"  FAIL: {step3['error']}")
                print(
                    "  CFF route closed for surface reports "
                    "(same class of failure as named expressions)."
                )
                return result
            result["steps"]["3_areaavg_cff"] = step3
            print(f"  cff_avg={cff_avg:.8g}")
            print("  PASS")

            # ---- Step 4: compare to UDM areaavg ----
            print("\n[4] Compare to surface-areaavg(udm)")
            # Prefer report field name (hyphen form used elsewhere in this repo).
            udm_field = FIELD_UDM_CM
            try:
                udm_avg = surface_areaavg(
                    solution, [clip], udm_field, f"udm_c{cell}"
                )
            except Exception:
                udm_field = cm_token
                udm_avg = surface_areaavg(
                    solution, [clip], udm_field, f"udm_c{cell}"
                )
            err = rel_err(cff_avg, udm_avg)
            step4 = {
                "pass": err < 1.0e-4,
                "udm_field": udm_field,
                "udm_avg": udm_avg,
                "cff_avg": cff_avg,
                "rel_err": err,
            }
            result["steps"]["4_compare_udm"] = step4
            print(
                f"  udm_avg={udm_avg:.8g} cff_avg={cff_avg:.8g} rel_err={err:.3e}"
            )
            print("  PASS" if step4["pass"] else "  FAIL (values disagree)")
            if not step4["pass"]:
                return result

            # ---- Step 5: full canon CFF ----
            print("\n[5] Nested canon CFF areaavg vs film-theory/k_N")
            c_b = csv_meta.get("c_b")
            if c_b is None:
                print("  FAIL: no c_b in wide CSV for this cell; skip nested compare")
                result["steps"]["5_canon"] = {
                    "pass": False,
                    "error": "missing c_b in summary_metrics_wide.csv",
                }
                return result
            c_b = float(c_b)
            jw_avg = surface_areaavg(
                solution, [clip], FIELD_UDM_JW, f"jw_c{cell}"
            )
            cm_avg = udm_avg
            cp_perm = film_theory_cp_perm_mol_m3(cm_avg, jw_avg, b_perm)
            k_n, _ = canonical_rescale_factor(c0, c_b, cp_perm)
            # Expected average-of-ratios from cell UDM fields:
            # areaavg( (cm - cp)/(cb - cp) ) is what the CFF should return if
            # Fluent evaluates face-wise. Compare also to k_N * areaavg(udm-9)
            # only if present in CSV.
            expr = (
                f"(({cm_token}) - (({b_perm}) * ({cm_token}) / "
                f"(({jw_token}) + ({b_perm})))) / "
                f"(({c_b}) - (({b_perm}) * ({cm_token}) / "
                f"(({jw_token}) + ({b_perm}))))"
            )
            scm_c = tmp_dir / f"{CANON_CFF_NAME}.scm"
            write_canon_scm(scm_c, CANON_CFF_NAME, cm_token, jw_token, b_perm, c_b)
            ok_c, how_c = try_load_cff(solver, scm_c)
            if not ok_c:
                ok_c, how_c = try_define_cff_expression(
                    solver, CANON_CFF_NAME, expr
                )
            if not ok_c:
                result["steps"]["5_canon"] = {
                    "pass": False,
                    "error": f"define failed: {how_c}",
                    "expression": expr,
                }
                print(f"  FAIL: could not define canon CFF ({how_c})")
                return result
            try:
                canon_cff_avg = surface_areaavg(
                    solution, [clip], CANON_CFF_NAME, f"canon_c{cell}"
                )
            except Exception as exc:
                result["steps"]["5_canon"] = {
                    "pass": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "expression": expr,
                    "define_method": how_c,
                }
                print(f"  FAIL: areaavg(canon CFF): {exc}")
                return result

            # Python reference from averages (scalar path): M_avg ≈ k_N * L2
            # is not identical to areaavg(canon faces); also report
            # (cm_avg - cp_perm)/(c_b - cp_perm) which equals k_N * (cm-cp)/(c0-cp)
            # only when L2 uses c0. Use film-theory average-order form:
            python_ratio = (cm_avg - cp_perm) / (c_b - cp_perm)
            csv_canon = csv_meta.get("cp_canon_cell")
            step5 = {
                "pass": True,
                "define_method": how_c,
                "expression": expr,
                "c_b": c_b,
                "cm_avg": cm_avg,
                "jw_avg": jw_avg,
                "cp_perm_avg": cp_perm,
                "python_ratio_of_avgs": python_ratio,
                "canon_cff_avg": canon_cff_avg,
                "rel_err_vs_ratio_of_avgs": rel_err(canon_cff_avg, python_ratio),
                "cp_canon_cell_csv": csv_canon,
            }
            if csv_canon is not None:
                step5["rel_err_vs_csv_canon"] = rel_err(
                    canon_cff_avg, float(csv_canon)
                )
            # Soft pass: CFF must be finite and positive; tight match to
            # ratio-of-averages is not required (AoR vs RoA).
            if not math.isfinite(canon_cff_avg) or canon_cff_avg <= 0.0:
                step5["pass"] = False
            result["steps"]["5_canon"] = step5
            print(f"  canon_cff_avg={canon_cff_avg:.8g}")
            print(f"  python_ratio_of_avgs={python_ratio:.8g}")
            if csv_canon is not None:
                print(f"  csv pp_cp_canon_cell_{cell}={csv_canon}")
            print("  PASS" if step5["pass"] else "  FAIL")
        finally:
            try:
                delete_iso_clip(solver, clip)
            except Exception:
                pass

        return result
    finally:
        if solver is not None:
            try:
                solver.exit(timeout=60)
            except Exception:
                try:
                    solver.force_exit()
                except Exception:
                    pass
        if meshing is not None:
            try:
                meshing.exit(timeout=60)
            except Exception:
                try:
                    meshing.force_exit()
                except Exception:
                    pass


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    case_dir = case_dir_for(args)
    print(f"case_dir={case_dir}")
    if args.dry_run_paths:
        cas = case_dir / f"{args.geo_id}_{args.run_id}_final.cas.h5"
        print(f"cas_exists={cas.is_file()} path={cas}")
        return 0 if cas.is_file() else 2

    result = run_probe(args)
    print("\n" + "=" * 72)
    print("SUMMARY")
    for key, step in (result.get("steps") or {}).items():
        status = "PASS" if step.get("pass") else "FAIL"
        print(f"  {key}: {status}")
    if result.get("error"):
        print(f"  error: {result['error']}")
    print("=" * 72)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {out}")

    steps = result.get("steps") or {}
    # Exit 0 if step 3 passed (CFF surface-report path is open), else 1.
    if steps.get("3_areaavg_cff", {}).get("pass"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
