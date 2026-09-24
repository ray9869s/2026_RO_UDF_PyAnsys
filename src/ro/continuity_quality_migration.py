"""Offline continuity-quality metadata correction for 95 extracted leaves.

Dry-run is the default. The only durable writes on ``--apply`` are the run
``manifest.json`` and ``post/reports/raw_report_values.json``. Extract skip
fingerprints, solver logs, the wide CSV, and final cas/dat are not modified.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, TextIO

from ro.convergence_quality import (
    _as_float,
    evaluate_convergence_quality,
    manifest_quality_payload,
    metrics_from_summary_row,
)
from ro.domain_layout import layout_from_run_directory
from ro.extract_skip import extract_skip_block_reason
from ro.manifest import ManifestError, read_run_manifest, update_run_manifest_fields
from ro.paths import project_root, require_existing_run, run_dir
from ro.residual_transcript import (
    PARSE_OK,
    list_transcript_candidates,
    parse_residual_table,
    read_text_replace,
    select_solve_transcript,
)

GROUP_ORIGINAL = "original_p6M"
GROUP_REPLACEMENT = "replacement"
GROUP_GTS_PILOT = "gts_pilot"

ORIGINAL_P6M_RUN_IDS = frozenset({"u0p1_p6M", "u0p2_p6M", "u0p3_p6M"})
EXPECTED_ORIGINAL_COUNT = 93
EXPECTED_TOTAL_COUNT = 95

EXPECTED_ORIGINAL_TRANSITIONS = {
    ("PASS", "PASS"): 69,
    ("FAIL", "PASS"): 17,
    ("FAIL", "FAIL"): 7,
}
EXPECTED_REPLACEMENT_TRANSITIONS = {("FAIL", "PASS"): 1}
EXPECTED_GTS_PILOT_TRANSITIONS = {("FAIL", "FAIL"): 1}
EXPECTED_ORIGINAL_TRANSITIONS_CORRECTED = {
    ("PASS", "PASS"): (
        EXPECTED_ORIGINAL_TRANSITIONS[("PASS", "PASS")]
        + EXPECTED_ORIGINAL_TRANSITIONS[("FAIL", "PASS")]
    ),
    ("FAIL", "FAIL"): EXPECTED_ORIGINAL_TRANSITIONS[("FAIL", "FAIL")],
}
EXPECTED_REPLACEMENT_TRANSITIONS_CORRECTED = {("PASS", "PASS"): 1}
EXPECTED_GTS_PILOT_TRANSITIONS_CORRECTED = dict(EXPECTED_GTS_PILOT_TRANSITIONS)
FIRST_RUN_TRANSITION_SNAPSHOT = {
    GROUP_ORIGINAL: EXPECTED_ORIGINAL_TRANSITIONS,
    GROUP_REPLACEMENT: EXPECTED_REPLACEMENT_TRANSITIONS,
    GROUP_GTS_PILOT: EXPECTED_GTS_PILOT_TRANSITIONS,
}
CORRECTED_TRANSITION_SNAPSHOT = {
    GROUP_ORIGINAL: EXPECTED_ORIGINAL_TRANSITIONS_CORRECTED,
    GROUP_REPLACEMENT: EXPECTED_REPLACEMENT_TRANSITIONS_CORRECTED,
    GROUP_GTS_PILOT: EXPECTED_GTS_PILOT_TRANSITIONS_CORRECTED,
}

REPLACEMENT_FAMILY = "sin"
REPLACEMENT_GEO_ID = "S_a144_l1733"
REPLACEMENT_MESH_ID = "max085_min006_cpg5_bl4_peel2"
REPLACEMENT_RUN_ID = "u0p3_p6M_restart"
REPLACEMENT_EXPECTED_CONTINUITY = 1.3233e-07
REPLACEMENT_EXPECTED_MASS_BALANCE_REL = -5.5605534102915826e-06
REPLACEMENT_EXPECTED_LMH_REL = 0.00011911564583713257

GTS_PILOT_FAMILY = "diamond"
GTS_PILOT_GEO_ID = "D0817_a30"
GTS_PILOT_MESH_ID = "max085_min006_cpg5_bl4_peel2"
GTS_PILOT_RUN_ID = "u0p3_p6M_ptgts3"
GTS_PILOT_EXPECTED_CONTINUITY = 0.015732

# Extractor derived_values quality keys (not including unavailable).
_RAW_REPORT_QUALITY_FIELDS = (
    ("convergence_quality", "convergence_quality"),
    ("needs_longer_solve", "needs_longer_solve"),
    ("convergence_quality_failures", "failures"),
    ("convergence_quality_warnings", "warnings"),
    ("continuity_final", "continuity_final"),
    ("pp_pressure_drop_rel_spread_window", "pp_pressure_drop_rel_spread_window"),
    ("pp_pressure_drop_rel_spread_cells_4_7", "pp_pressure_drop_rel_spread_cells_4_7"),
    ("pp_pressure_drop_rel_spread_note", "pp_pressure_drop_rel_spread_note"),
)

_FLOAT_REL_TOL = 1e-4
_FLOAT_ABS_TOL = 1e-16


@dataclass(frozen=True)
class MigrationTarget:
    group: str
    family: str
    geo_id: str
    mesh_id: str
    run_id: str

    @property
    def four_id(self) -> str:
        return f"{self.family}/{self.geo_id}/{self.mesh_id}/{self.run_id}"


REPLACEMENT_TARGET = MigrationTarget(
    GROUP_REPLACEMENT,
    REPLACEMENT_FAMILY,
    REPLACEMENT_GEO_ID,
    REPLACEMENT_MESH_ID,
    REPLACEMENT_RUN_ID,
)
GTS_PILOT_TARGET = MigrationTarget(
    GROUP_GTS_PILOT,
    GTS_PILOT_FAMILY,
    GTS_PILOT_GEO_ID,
    GTS_PILOT_MESH_ID,
    GTS_PILOT_RUN_ID,
)


@dataclass
class LeafAssessment:
    target: MigrationTarget
    error: Optional[str] = None
    saved_quality: Optional[str] = None
    corrected_quality: Optional[str] = None
    continuity_final: Optional[float] = None
    lmh_relative_difference: Optional[float] = None
    mass_balance_relative_error: Optional[float] = None
    last_iter: Optional[int] = None
    log_name: Optional[str] = None
    quality_result: Optional[dict[str, Any]] = None


def raw_report_quality_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Quality keys written into raw_report_values.json derived_values."""
    return {json_key: result[result_key] for json_key, result_key in _RAW_REPORT_QUALITY_FIELDS}


def canonical_solver_log_name(run_id: str) -> str:
    return f"solver_log_{run_id}.txt"


def load_batch_config():
    path = project_root() / "configs" / "batch_config.py"
    spec = importlib.util.spec_from_file_location(
        "batch_config_continuity_quality_migration",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load batch_config from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_targets(
    production_cases: Optional[Sequence[Mapping[str, Any]]] = None,
) -> list[MigrationTarget]:
    """Exactly 93 original p6M production leaves plus replacement and GTS pilot."""
    if production_cases is None:
        production_cases = load_batch_config().production_solver_sweep_cases
    original = [
        MigrationTarget(
            GROUP_ORIGINAL,
            case["family"],
            case["geo_id"],
            case["mesh_id"],
            case["run_id"],
        )
        for case in production_cases
        if case["run_id"] in ORIGINAL_P6M_RUN_IDS
    ]
    if len(original) != EXPECTED_ORIGINAL_COUNT:
        raise RuntimeError(
            "original p6M selection must be "
            f"{EXPECTED_ORIGINAL_COUNT} leaves, got {len(original)}."
        )
    targets = original + [REPLACEMENT_TARGET, GTS_PILOT_TARGET]
    four_ids = [target.four_id for target in targets]
    if len(four_ids) != len(set(four_ids)):
        raise RuntimeError("migration targets are not unique.")
    if len(targets) != EXPECTED_TOTAL_COUNT:
        raise RuntimeError(
            f"migration selection must be {EXPECTED_TOTAL_COUNT} leaves, "
            f"got {len(targets)}."
        )
    return targets


def inspect_leaf(target: MigrationTarget) -> LeafAssessment:
    try:
        directory = require_existing_run(
            target.family, target.geo_id, target.mesh_id, target.run_id
        )
    except (FileNotFoundError, ValueError) as exc:
        return LeafAssessment(target, error=str(exc))

    cas = directory / f"{target.geo_id}_{target.run_id}_final.cas.h5"
    dat = directory / f"{target.geo_id}_{target.run_id}_final.dat.h5"
    reports = directory / "post" / "reports"
    wide_csv = reports / "summary_metrics_wide.csv"
    raw_json = reports / "raw_report_values.json"

    skip_block = extract_skip_block_reason(directory, wide_csv, cas, dat)
    if skip_block is not None:
        return LeafAssessment(
            target, error=f"extract skip evidence missing: {skip_block}"
        )

    try:
        record, run_payload = layout_from_run_directory(directory)
        evaluation_cells = record.evaluation_window.evaluation_cell_numbers(
            record.layout
        )
    except (OSError, ManifestError, ValueError, TypeError) as exc:
        return LeafAssessment(
            target, error=f"evaluation-window layout unavailable: {exc}"
        )
    if not evaluation_cells:
        return LeafAssessment(target, error="evaluation-window layout is empty")

    saved_quality = run_payload.get("convergence_quality")
    if saved_quality not in ("PASS", "FAIL"):
        return LeafAssessment(
            target,
            error=(
                "saved convergence_quality must be PASS or FAIL, "
                f"got {saved_quality!r}"
            ),
        )

    canonical_name = canonical_solver_log_name(target.run_id)
    canonical_path = directory / canonical_name
    if not canonical_path.is_file():
        return LeafAssessment(
            target, error=f"canonical solver log not found: {canonical_name}"
        )

    selected, status, detail = select_solve_transcript(directory)
    if selected is None or status != PARSE_OK:
        return LeafAssessment(
            target,
            error=f"could not uniquely select canonical solver log: {status}: {detail}",
        )
    if selected.name != canonical_name:
        return LeafAssessment(
            target,
            error=(
                "selected solver log is "
                f"{selected.name}, expected {canonical_name}"
            ),
        )

    competing = _competing_residual_logs(directory, selected)
    if competing:
        names = ", ".join(path.name for path in competing)
        return LeafAssessment(
            target,
            error=f"competing residual-table log(s): {names}",
        )

    text, read_err = read_text_replace(selected)
    if text is None:
        return LeafAssessment(
            target, error=f"canonical solver log unreadable: {read_err}"
        )
    rows, table_detail = parse_residual_table(text)
    if not rows:
        return LeafAssessment(
            target, error=f"canonical solver log has no residual table: {table_detail}"
        )
    if "unparsed_later_residual_rows=" in table_detail:
        return LeafAssessment(
            target,
            error=f"canonical solver log is not trustworthy: {table_detail}",
        )
    continuity = _as_float(rows[-1].get("continuity"))
    if continuity is None:
        return LeafAssessment(
            target, error="final residual row continuity is not finite"
        )

    wide_row, wide_err = _first_wide_row(wide_csv)
    if wide_err is not None:
        return LeafAssessment(target, error=wide_err)
    metrics = metrics_from_summary_row(wide_row)
    lmh = _as_float(metrics.get("lmh_relative_difference"))
    mb = _as_float(metrics.get("mass_balance_relative_error"))
    if lmh is None:
        return LeafAssessment(
            target, error="lmh_relative_difference is missing or not finite"
        )
    if mb is None:
        return LeafAssessment(
            target, error="mass_balance_relative_error is missing or not finite"
        )

    if not raw_json.is_file():
        return LeafAssessment(
            target, error=f"raw_report_values.json not found: {raw_json}"
        )
    try:
        raw_payload = json.loads(raw_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return LeafAssessment(
            target, error=f"raw_report_values.json not usable: {exc}"
        )
    if not isinstance(raw_payload, dict) or not isinstance(
        raw_payload.get("derived_values"), dict
    ):
        return LeafAssessment(
            target,
            error="raw_report_values.json must contain a derived_values object",
        )

    quality_result = evaluate_convergence_quality(
        metrics,
        continuity_final=continuity,
        evaluation_cell_numbers=evaluation_cells,
    )
    return LeafAssessment(
        target,
        saved_quality=saved_quality,
        corrected_quality=quality_result["convergence_quality"],
        continuity_final=quality_result["continuity_final"],
        lmh_relative_difference=quality_result["lmh_relative_difference"],
        mass_balance_relative_error=quality_result["mass_balance_relative_error"],
        last_iter=int(rows[-1]["iter"]),
        log_name=selected.name,
        quality_result=quality_result,
    )


def transition_counts(assessments: Sequence[LeafAssessment]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {
        GROUP_ORIGINAL: Counter(),
        GROUP_REPLACEMENT: Counter(),
        GROUP_GTS_PILOT: Counter(),
    }
    for assessment in assessments:
        if assessment.error is not None:
            continue
        if assessment.saved_quality is None or assessment.corrected_quality is None:
            continue
        counts[assessment.target.group][
            (assessment.saved_quality, assessment.corrected_quality)
        ] += 1
    return counts


def expected_transition_block_reason(
    assessments: Sequence[LeafAssessment],
) -> Optional[str]:
    """None when counts match the first-run audit or the fully corrected state."""
    errors = [item for item in assessments if item.error]
    if errors:
        return (
            f"{len(errors)} leaf requirement(s) failed; "
            "refusing to apply any changes"
        )
    snapshot = _transition_snapshot(assessments)
    if (
        snapshot != FIRST_RUN_TRANSITION_SNAPSHOT
        and snapshot != CORRECTED_TRANSITION_SNAPSHOT
    ):
        return (
            "transitions match neither the pre-migration audit "
            f"({_format_snapshot(FIRST_RUN_TRANSITION_SNAPSHOT)}) "
            "nor the fully corrected post-migration state "
            f"({_format_snapshot(CORRECTED_TRANSITION_SNAPSHOT)}); "
            f"got {_format_snapshot(snapshot)}. "
            "Partial or unexpected quality metadata; restore "
            "manifest.json and post/reports/raw_report_values.json "
            "from their .bak.* backups and rerun. Refusing to apply."
        )

    replacement_leaf = _unique_group_leaf(assessments, GROUP_REPLACEMENT)
    if replacement_leaf is None:
        return "replacement leaf is missing from assessments"
    replacement_err = _replacement_value_block_reason(replacement_leaf)
    if replacement_err is not None:
        return replacement_err

    pilot_leaf = _unique_group_leaf(assessments, GROUP_GTS_PILOT)
    if pilot_leaf is None:
        return "gts_pilot leaf is missing from assessments"
    if not _close(pilot_leaf.continuity_final, GTS_PILOT_EXPECTED_CONTINUITY):
        return (
            "gts_pilot corrected continuity "
            f"{pilot_leaf.continuity_final!r} != {GTS_PILOT_EXPECTED_CONTINUITY}"
        )
    if pilot_leaf.corrected_quality != "FAIL":
        return (
            "gts_pilot must remain FAIL, "
            f"got {pilot_leaf.corrected_quality!r}"
        )
    return None


def apply_leaf(assessment: LeafAssessment) -> str:
    """Update manifest + raw JSON together. Returns 'unchanged' or 'wrote'."""
    if assessment.error is not None or assessment.quality_result is None:
        raise RuntimeError(
            f"refusing to apply incomplete assessment for {assessment.target.four_id}"
        )
    target = assessment.target
    directory = require_existing_run(
        target.family, target.geo_id, target.mesh_id, target.run_id
    )
    manifest_path = directory / "manifest.json"
    json_path = directory / "post" / "reports" / "raw_report_values.json"
    manifest_updates = manifest_quality_payload(assessment.quality_result)
    json_updates = raw_report_quality_payload(assessment.quality_result)

    existing_manifest = read_run_manifest(directory)
    existing_raw = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(existing_raw, dict) or not isinstance(
        existing_raw.get("derived_values"), dict
    ):
        raise RuntimeError(
            f"{target.four_id}: raw_report_values.json lost derived_values before apply"
        )
    if _payload_matches(existing_manifest, manifest_updates) and _payload_matches(
        existing_raw["derived_values"], json_updates
    ):
        return "unchanged"

    merged_raw = dict(existing_raw)
    merged_derived = dict(existing_raw["derived_values"])
    merged_derived.update(json_updates)
    merged_raw["derived_values"] = merged_derived

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest_backup = _backup_file(manifest_path, stamp)
    json_backup = _backup_file(json_path, stamp)
    try:
        update_run_manifest_fields(directory, manifest_updates)
        _atomic_write_json(json_path, merged_raw)
    except Exception:
        _atomic_restore(manifest_backup, manifest_path)
        _atomic_restore(json_backup, json_path)
        raise
    return "wrote"


def run_migration(
    *,
    apply: bool,
    targets: Optional[Sequence[MigrationTarget]] = None,
    check_expected_transitions: bool = True,
    stream: Optional[TextIO] = None,
) -> int:
    """Assess all leaves, then apply only when every gate passes. 0 = success."""
    out = stream if stream is not None else sys.stdout
    if targets is None:
        targets = migration_targets()
    _print_scope(targets, out)
    assessments = [inspect_leaf(target) for target in targets]
    for assessment in assessments:
        _print_leaf(assessment, out)

    counts = transition_counts(assessments)
    for group in (GROUP_ORIGINAL, GROUP_REPLACEMENT, GROUP_GTS_PILOT):
        if counts[group] or check_expected_transitions:
            print(
                f"TRANSITIONS {group} {_format_transitions(counts[group])}",
                file=out,
            )

    abort = None
    if any(item.error for item in assessments) or check_expected_transitions:
        abort = expected_transition_block_reason(assessments)

    if abort is not None:
        print(f"ABORT {abort}", file=out)
        print(
            "DRY_RUN no files written"
            if not apply
            else "APPLY aborted; no files written",
            file=out,
        )
        return 1

    if not apply:
        print("DRY_RUN no files written", file=out)
        return 0

    wrote = 0
    unchanged = 0
    for assessment in assessments:
        try:
            status = apply_leaf(assessment)
        except Exception as exc:
            print(
                f"ABORT {assessment.target.four_id}: apply failed: "
                f"{type(exc).__name__}: {exc}",
                file=out,
            )
            print(
                f"APPLY wrote={wrote} unchanged={unchanged} failed=1",
                file=out,
            )
            return 1
        if status == "wrote":
            wrote += 1
        else:
            unchanged += 1
    print(f"APPLY wrote={wrote} unchanged={unchanged}", file=out)
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute continuity-quality metadata for 95 extracted p6M leaves. "
            "Dry-run by default; pass --apply to write manifest.json and "
            "raw_report_values.json together."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the quality pair (default is dry-run; writes nothing).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    return run_migration(apply=args.apply)


def _is_solver_log_txt(path: Path) -> bool:
    return path.name.lower().startswith("solver_log_") and path.suffix.lower() == ".txt"


def _competing_residual_logs(directory: Path, selected: Path) -> list[Path]:
    """Other table-bearing solver_log_*.txt files, not Fluent *.trn transcripts."""
    competing: list[Path] = []
    selected_resolved = selected.resolve()
    for path in list_transcript_candidates(directory):
        if path.resolve() == selected_resolved:
            continue
        if not _is_solver_log_txt(path):
            continue
        text, err = read_text_replace(path)
        if text is None or err is not None:
            continue
        rows, _detail = parse_residual_table(text)
        if rows:
            competing.append(path)
    return competing


def _first_wide_row(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            row = next(reader, None)
    except OSError as exc:
        return None, f"summary_metrics_wide.csv not readable: {exc}"
    if row is None:
        return None, "summary_metrics_wide.csv has no data rows"
    return row, None


def _unique_group_leaf(
    assessments: Sequence[LeafAssessment],
    group: str,
) -> Optional[LeafAssessment]:
    matches = [item for item in assessments if item.target.group == group]
    if len(matches) != 1:
        return None
    return matches[0]


def _replacement_value_block_reason(leaf: LeafAssessment) -> Optional[str]:
    if not _close(leaf.continuity_final, REPLACEMENT_EXPECTED_CONTINUITY):
        return (
            "replacement corrected continuity "
            f"{leaf.continuity_final!r} != {REPLACEMENT_EXPECTED_CONTINUITY}"
        )
    if not _close(leaf.mass_balance_relative_error, REPLACEMENT_EXPECTED_MASS_BALANCE_REL):
        return (
            "replacement mass-balance relative error "
            f"{leaf.mass_balance_relative_error!r} != "
            f"{REPLACEMENT_EXPECTED_MASS_BALANCE_REL}"
        )
    if not _close(leaf.lmh_relative_difference, REPLACEMENT_EXPECTED_LMH_REL):
        return (
            "replacement LMH relative difference "
            f"{leaf.lmh_relative_difference!r} != {REPLACEMENT_EXPECTED_LMH_REL}"
        )
    return None


def _close(actual: Any, expected: float) -> bool:
    parsed = _as_float(actual)
    if parsed is None:
        return False
    return math.isclose(
        parsed,
        expected,
        rel_tol=_FLOAT_REL_TOL,
        abs_tol=_FLOAT_ABS_TOL,
    )


def _transition_snapshot(
    assessments: Sequence[LeafAssessment],
) -> dict[str, dict[tuple[str, str], int]]:
    counts = transition_counts(assessments)
    return {
        group: dict(counts[group])
        for group in (GROUP_ORIGINAL, GROUP_REPLACEMENT, GROUP_GTS_PILOT)
    }


def _format_transitions(counts: Mapping[tuple[str, str], int]) -> str:
    parts = [
        f"{saved}->{corrected}={n}"
        for (saved, corrected), n in sorted(counts.items())
    ]
    return " ".join(parts) if parts else "(none)"


def _format_snapshot(
    snapshot: Mapping[str, Mapping[tuple[str, str], int]],
) -> str:
    return "; ".join(
        f"{group} {_format_transitions(snapshot[group])}"
        for group in (GROUP_ORIGINAL, GROUP_REPLACEMENT, GROUP_GTS_PILOT)
    )


def _print_scope(targets: Sequence[MigrationTarget], out: TextIO) -> None:
    by_group: dict[str, list[MigrationTarget]] = {
        GROUP_ORIGINAL: [],
        GROUP_REPLACEMENT: [],
        GROUP_GTS_PILOT: [],
    }
    for target in targets:
        by_group[target.group].append(target)
    print(f"SCOPE {GROUP_ORIGINAL} n={len(by_group[GROUP_ORIGINAL])}", file=out)
    for group in (GROUP_REPLACEMENT, GROUP_GTS_PILOT):
        leaves = by_group[group]
        extra = ""
        if len(leaves) == 1:
            extra = f" four-id={leaves[0].four_id}"
        print(f"SCOPE {group} n={len(leaves)}{extra}", file=out)


def _print_leaf(assessment: LeafAssessment, out: TextIO) -> None:
    target = assessment.target
    if assessment.error is not None:
        print(f"ERROR {target.group} {target.four_id}: {assessment.error}", file=out)
        return
    continuity = (
        f"{assessment.continuity_final:.6e}"
        if assessment.continuity_final is not None
        else "None"
    )
    print(
        f"LEAF {target.group} {target.four_id} "
        f"saved={assessment.saved_quality} corrected={assessment.corrected_quality} "
        f"continuity={continuity} iter={assessment.last_iter} "
        f"log={assessment.log_name}",
        file=out,
    )


def _payload_matches(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    for key, value in incoming.items():
        if not _values_equal(existing.get(key), value):
            return False
    return True


def _values_equal(left: Any, right: Any) -> bool:
    if left is right:
        return True
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        parsed_right = _as_float(right)
        parsed_left = _as_float(left)
        if parsed_left is None or parsed_right is None:
            return False
        return math.isclose(parsed_left, parsed_right, rel_tol=1e-12, abs_tol=0.0)
    if isinstance(left, list) and isinstance(right, list):
        return left == right
    return left == right


def _backup_file(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.name}.bak.{stamp}")
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = path.with_name(f"{path.name}.bak.{stamp}.{suffix}")
    shutil.copy2(path, candidate)
    return candidate


def _atomic_restore(backup: Path, dest: Path) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dest.parent,
            prefix=f".{dest.name}.restore.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
        shutil.copy2(backup, tmp_path)
        os.replace(tmp_path, dest)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:
        raise OSError(f"Could not write {path}: {exc}") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return path
