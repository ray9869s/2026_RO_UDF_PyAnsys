"""Host-only sequential checks. --plan and queue emission never start Fluent.

The shell entry point establishes the Windows venv and clean environment.
Each live step checks for other queue/Fluent processes before touching a leaf.
No long solve is launched here. All previous solutions/reports are preserved.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import campaign_v3 as campaign
import campaign_v3_reextract as reextract
from mfbo import job_queue
from ro.child_output import subprocess_text_kwargs
from ro.host_lock import host_lock_path
from ro.mesh_common import load_mesh_run_record
from ro.mesh_spec import default_spec_path, family_settings, load_mesh_spec
from ro.paths import mesh_dir, project_root, refuse_production_root, run_dir, using_data_root
from ro.residual_transcript import parse_residual_table, select_solve_transcript
from ro.solve_checkpoint import list_checkpoints
from ro.monitor_change import read_report_series

ROOT = Path(campaign.DATA_ROOT)
BASE_MESH = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
FAILED_GEOS = ("D1225_a30", "S_a072_l1733", "S_a144_l1733", "S_a193_l1733")
REPRESENTATIVES = ("REF_empty", "D2450_a45", "M_c267", "P_p80_h15", "S_a144_l3465")


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require_file(path):
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Missing/empty file: {path}")


def run_leaf(root, geo, mesh=BASE_MESH, case="u0p2_p6M"):
    with using_data_root(root):
        return run_dir(campaign.family_for_geo_id(geo), geo, mesh, case)


def mesh_leaf(root, geo, mesh):
    with using_data_root(root):
        return mesh_dir(campaign.family_for_geo_id(geo), geo, mesh)


def windows_processes():
    if os.name != "nt":
        raise RuntimeError("Live checks require the Windows host Python, not WSL.")
    result = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,CommandLine) | ConvertTo-Json -Compress",
    ], capture_output=True, check=True, timeout=30, **subprocess_text_kwargs())
    payload = json.loads(result.stdout.lstrip("\ufeff"))
    return payload if isinstance(payload, list) else [payload]


def require_idle(root, *, processes=None):
    processes = windows_processes() if processes is None else processes
    busy = []
    workers = ("mesh_study_case.py", "solver_code_", "meshing_code_",
               "pyfluent_report_extract.py", "locate_mass_imbalance.py", "reextract_runs.py",
               "campaign_v3_reextract.py")
    for process in processes:
        pid = process.get("ProcessId")
        if pid == os.getpid():
            continue
        name = str(process.get("Name", "")).lower()
        command = process.get("CommandLine")
        if re.match(r"^(?:fluent|fl_mpi|cortex|fl\d).*\.exe$", name):
            busy.append(f"pid={pid} {name}")
        elif name in ("python.exe", "pythonw.exe"):
            if not command:
                raise RuntimeError(f"Cannot inspect Python command line for pid {pid}")
            lower = command.lower()
            if (("job_queue.py" in lower and re.search(r"\brun\b", lower))
                    or any(worker in lower for worker in workers)):
                busy.append(f"pid={pid} {command}")
    host_lock = host_lock_path()
    locks = [*(Path(root) / "queues").rglob("*.lock"), *([host_lock] if host_lock.is_file() else [])]
    for lock in locks:
        text = lock.read_text(encoding="ascii").strip()
        if not text.isdecimal() or int(text) <= 0:
            raise RuntimeError(f"Unrecognised queue lock: {lock}")
        pid = int(text)
        if pid != os.getpid() and job_queue.pid_is_alive(pid):
            busy.append(f"live queue lock pid={pid}: {lock}")
    if busy:
        raise RuntimeError("Host is busy; refusing another Fluent session: " + "; ".join(busy))


def move_absent(source, destination):
    """Rename only; never merge, replace, delete, or fall back to copying."""
    refuse_production_root(source)
    refuse_production_root(destination)
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Expected a real leaf directory: {source}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to replace archive/restored leaf: {destination}")
    source.rename(destination)


BROKEN_MARKER = "BROKEN"


def write_broken_markers(leaf, payload):
    """``<leaf>.BROKEN.json`` beside the leaf, and ``<leaf>/BROKEN`` inside it.

    The inside marker makes reporting refuse whatever now sits at the leaf
    path (``ro.report_binding``). Returns the marker paths written.
    """
    text = json.dumps(payload, indent=2) + "\n"
    written = []
    beside = leaf.with_name(f"{leaf.name}.{BROKEN_MARKER}.json")
    beside.write_text(text, encoding="utf-8")
    written.append(str(beside))
    if leaf.is_dir() and not leaf.is_symlink():
        inside = leaf / BROKEN_MARKER
        inside.write_text(text, encoding="utf-8")
        written.append(str(inside))
    return written


def require_final(leaf, geo, case):
    for extension in ("cas", "dat"):
        require_file(leaf / f"{geo}_{case}_final.{extension}.h5")
    manifest = read_json(leaf / "manifest.json")
    iterations = manifest.get("iterations_run")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations <= 20:
        raise ValueError(f"Expected a finished solution beyond the smoke cap: {leaf}")


def validate_smoke(leaf, case="u0p2_p6M", *, cp_live=True, details=None):
    transcript, _status, detail = select_solve_transcript(leaf)
    rows = []
    if transcript is not None:
        rows, detail = parse_residual_table(transcript.read_text(encoding="utf-8", errors="replace"))
    iterations = [int(row["iter"]) for row in rows]
    checkpoints = [pair.iteration for pair in list_checkpoints(leaf, "REF_empty", case)
                   if pair.case_path.stat().st_size > 0 and pair.data_path.stat().st_size > 0]
    if details is not None:
        details.update(residual_rows=len(rows), last_iteration=iterations[-1] if rows else None,
                       checkpoints=checkpoints)
    wide = reextract.read_wide(leaf)
    flux = float(wide["lmh_window_exposed"])
    metrics = {"residual_rows": len(rows), "last_iteration": iterations[-1] if rows else None,
               "checkpoints": checkpoints, "window_flux_LMH": flux}
    if details is not None:
        details.update(metrics)
    if iterations != list(range(1, 21)) or "unparsed_later_residual_rows=" in detail:
        raise ValueError(f"Smoke needs exactly residual iterations 1-20; {metrics}; {detail}")
    if checkpoints != [10, 20]:
        raise ValueError(f"Smoke checkpoint pairs 10/20 missing or empty: {metrics}")
    if not math.isfinite(flux) or flux <= 0:
        raise ValueError(f"Smoke window flux is not positive: {metrics}")
    if cp_live and not read_report_series(leaf / "cp_live.out"):
        raise ValueError(f"Smoke cp_live report history is missing: {leaf}")
    return metrics


class Checks:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.stamp = utc_stamp()
        self.logs = self.root / "queues" / "logs"
        self.summary = self.logs / "pending_checks_summary.txt"
        self.attempt = self.logs / f"pending_checks_{self.stamp}"
        self.cp_live_passed = False
        self.passing_faces = []
        self.results = []

    def command(self, geo, *, case="u0p2_p6M", mesh_only=False, face=None, ar=None):
        spec = load_mesh_spec(default_spec_path())
        if spec["geometries"]:
            raise ValueError("Pending checks require the standard spec with geometries={}; use a separate spec for follow-ups.")
        if campaign.mesh_ids_for_spec(spec)[geo] != BASE_MESH:
            raise ValueError(f"Pending checks require the standard campaign sizing for {geo}")
        settings = family_settings(spec, campaign.family_for_geo_id(geo))
        argv = campaign._study_argv(
            geo, settings, case, mesh_only=mesh_only, reuse_mesh=not mesh_only,
            spec_token="configs/mesh_spec_v3.json", max_aspect_ratio=ar,
            membrane_face_size=None if face is None else {
                "labels": ["wall_top_mem", "wall_bottom_mem"], "size_mm": face, "growth_rate": 1.2,
            },
        )
        return [sys.executable if token == "{python}" else token for token in argv]

    def run_logged(self, command, name):
        path = self.attempt / f"{name}.log"
        print("RUN " + shlex.join(command), flush=True)
        with path.open("x", encoding="utf-8") as log:
            with subprocess.Popen(command, cwd=project_root(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, **subprocess_text_kwargs()) as child:
                for line in child.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end="", flush=True)
                return child.wait()

    def smoke(self, details, *, case="u0p2_p6M", cp_live=True, name="1_cp_live_smoke"):
        leaf = run_leaf(self.root, "REF_empty", case=case)
        mesh = mesh_leaf(self.root, "REF_empty", BASE_MESH)
        require_file(mesh / "manifest.json")
        record = load_mesh_run_record(mesh / "mesh_run_record.json")
        if not record or record.get("status") != "SUCCESS":
            raise ValueError("REF_empty standard mesh does not record SUCCESS")
        command = self.command("REF_empty", case=case)
        command[command.index("--max-iterations") + 1] = "20"
        command[command.index("--checkpoint-every") + 1] = "10"
        command.append("--keep-checkpoints")
        if not cp_live:
            command.append("--no-cp-live")
        original = leaf.with_name(f"{leaf.name}.prev_{self.stamp}")
        smoke_archive = leaf.with_name(f"{leaf.name}.smoke.prev_{self.stamp}")
        had_original = leaf.exists()
        if case == "u0p2_p6M" and not had_original:
            raise ValueError(f"Finished REF_empty leaf is missing: {leaf}")
        if had_original:
            require_final(leaf, "REF_empty", case)
        if original.exists() or smoke_archive.exists():
            raise FileExistsError("Smoke archive already exists; refusing ambiguous leaves")
        recovery = self.attempt / f"{name}_recovery.json"
        recovery.write_text(json.dumps({"leaf": str(leaf), "original": str(original),
                                        "smoke_archive": str(smoke_archive), "had_original": had_original}, indent=2), encoding="utf-8")
        moved = False
        try:
            if had_original:
                move_absent(leaf, original)  # An error aborts this step before launch.
                moved = True
            details["original_backup"] = str(original) if moved else "absent"
            code = self.run_logged(command, name)
            details["return_code"] = code
            details.update(validate_smoke(leaf, case, cp_live=cp_live, details=details))
            if code:
                raise RuntimeError(f"Smoke command exited {code}")
        finally:
            if moved or not had_original:
                self.restore_after_smoke(leaf, original, smoke_archive, moved, details)

    def restore_after_smoke(self, leaf, original, smoke_archive, moved, details):
        """Archive the smoke, then put the original back; never delete.

        Each rename is tried even when the other fails. A failed restore
        leaves a BROKEN marker so no report reads the smoke as the leaf.
        """
        errors = []
        try:
            # Preserve all generated smoke artifacts, even on validation failure.
            if leaf.exists():
                move_absent(leaf, smoke_archive)
                details["smoke_archive"] = str(smoke_archive)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised below
            errors.append(f"smoke archive: {type(exc).__name__}: {exc}")
        finally:
            if moved:
                try:
                    move_absent(original, leaf)
                    details["original_restored"] = True
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"restore original: {type(exc).__name__}: {exc}")
        if errors:
            details["original_restored"] = details.get("original_restored", False)
            details["broken_markers"] = write_broken_markers(
                leaf, {"leaf": str(leaf), "original": str(original),
                       "smoke_archive": str(smoke_archive), "errors": errors})
            raise RuntimeError("Smoke restore failed: " + "; ".join(errors))

    def mesh(self, details, geo, *, face=None, ar=None):
        mesh_id = BASE_MESH + ("_ar250" if ar else "")
        if face is not None:
            mesh_id = BASE_MESH.replace("_y1h", f"_fs{round(face * 1000):03d}_y1h")
        leaf = mesh_leaf(self.root, geo, mesh_id)
        details.update(geo_id=geo, mesh_id=mesh_id)
        if leaf.exists():
            raise FileExistsError(f"Mesh leaf already exists; preserve/move it aside first: {leaf}")
        code = self.run_logged(self.command(geo, mesh_only=True, face=face, ar=ar), f"mesh_{geo}_{mesh_id}")
        details["return_code"] = code
        manifest = leaf / "manifest.json"
        metrics = read_json(manifest) if manifest.is_file() else campaign._metrics_from_log(
            leaf / f"mesh_log_{mesh_id}.txt"
        ) or {}
        details.update(cells=metrics.get("cell_count"), max_AR=metrics.get("AR_max"),
                       min_orthogonal_quality=metrics.get("ortho_min"))
        gate = campaign.quality_gate(
            metrics.get("ortho_min"), metrics.get("AR_max"), metrics.get("skewness_max"),
            metrics.get("skewed_face_fraction"), aspect_split=metrics.get("aspect_split"),
            prism_ar_limit=None if ar else campaign.PRISM_AR_MAX_LIMIT,
            ar_limit=ar or campaign.AR_MAX_LIMIT,
        )
        details["gate"] = gate
        record = load_mesh_run_record(leaf / "mesh_run_record.json")
        if code or gate != "PASS" or not record or record.get("status") != "SUCCESS":
            raise RuntimeError(f"Mesh failed: {details}; worker_error={(record or {}).get('error_summary')}")
        require_file(leaf / f"{geo}_{mesh_id}.msh.h5")
        if face is not None and float(metrics["AR_max"]) <= campaign.AR_MAX_LIMIT:
            self.passing_faces.append(face)

    def locate(self, details):
        source = run_leaf(self.root, "D2450_a30")
        require_final(source, "D2450_a30", "u0p2_p6M")
        copy_root = Path("C:/ro_data_mfbo/mass_imbalance") / f"pending_{self.stamp}"
        if copy_root.exists():
            raise FileExistsError(f"Copy root already exists: {copy_root}")
        details["copy_root"] = str(copy_root)
        code = self.run_logged([
            sys.executable, "scripts/mfbo/locate_mass_imbalance.py", "--source", str(source),
            "--data-root", str(copy_root),
        ], "4_locate_D2450_a30")
        details["return_code"] = code
        if code:
            raise RuntimeError(f"locate_mass_imbalance exited {code}")
        report = copy_root / "reports" / "D2450_a30" / BASE_MESH / "u0p2_p6M"
        summary = read_json(report / "summary.json")
        details.update(cells=summary["cell_count"], total_abs_imbalance=summary["total_abs_imbalance"],
                       bins=len(summary["histogram"]))
        print((report / "histogram.csv").read_text(encoding="utf-8"), flush=True)
        print("REGION BINS " + json.dumps(summary.get("regions", [])), flush=True)

    def timed_reextract(self, details):
        leaf = reextract.require_pass_final(run_leaf(self.root, "P_p80_h15"), self.root)
        timing_path = leaf / "post" / "reports" / "report_extract_timing.json"
        def timing_identity():
            if not timing_path.is_file():
                return None
            stat = timing_path.stat()
            return stat.st_ino, stat.st_mtime_ns, stat.st_size
        before_timing = timing_identity()
        code = self.run_logged([
            sys.executable, "scripts/campaign_v3_reextract.py", "--data-root", str(self.root),
            "--run-leaf", str(leaf),
        ], "5_timed_reextract_P_p80_h15")
        details["return_code"] = code
        if timing_path.is_file() and timing_identity() != before_timing:
            details["extract_wall_time_s"] = read_json(timing_path).get("total_seconds")
        if code:
            raise RuntimeError(f"Timed full re-extract exited {code}")
        values = reextract.validate_meanfirst_summary(leaf)
        details.update({key: values[key] for key in reextract.NEW_CP_COLUMNS})
        details["post_extract_gate"] = read_json(leaf / "manifest.json").get("convergence_quality")
        seconds = details.get("extract_wall_time_s")
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("New extraction wall time is missing/non-positive")
        if details["post_extract_gate"] != "PASS":
            raise ValueError("Timed leaf no longer passes the post-extract convergence gate")

    def step(self, name, action):
        started = time.monotonic()
        if "smoke" in name:
            details = {"residual_rows": None, "checkpoints": [], "window_flux_LMH": None}
        elif "_fs" in name or "_ar250" in name:
            details = {"cells": None, "max_AR": None, "min_orthogonal_quality": None, "gate": "MISSING"}
        elif "reextract" in name:
            details = {"extract_wall_time_s": None}
        else:
            details = {"cells": None, "total_abs_imbalance": None, "bins": None}
        status = "PASS"
        try:
            require_idle(self.root)
            action(details)
        except Exception as exc:
            status = "FAIL"
            details["error"] = f"{type(exc).__name__}: {exc}"
        details["elapsed_s"] = round(time.monotonic() - started, 3)
        self.record(name, status, details)
        return status == "PASS"

    def record(self, name, status, details):
        # json.dumps escapes embedded CR/LF: exactly one append-only line per step.
        line = f"{utc_stamp()} {status} {name} " + json.dumps(details, ensure_ascii=True, sort_keys=True)
        with self.summary.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)
        self.results.append(status)

    def run(self, *, velocity_smoke=False, no_cp_live=False):
        self.logs.mkdir(parents=True, exist_ok=True)
        self.attempt.mkdir(exist_ok=False)
        lock = self.root / "queues" / "pending_checks.lock"
        actions = [("velocity_smoke", lambda details: self.smoke(
            details, case="u0p05_p6M", cp_live=not no_cp_live, name="velocity_smoke"
        ))] if velocity_smoke else [
            ("1_cp_live_smoke", self.smoke),
            ("2_fs025", lambda details: self.mesh(details, "S_a193_l1733", face=0.025)),
            ("2_fs020", lambda details: self.mesh(details, "S_a193_l1733", face=0.020)),
            ("3_ar250_D1225_a30", lambda details: self.mesh(details, "D1225_a30", ar=250)),
            ("3_ar250_S_a193_l1733", lambda details: self.mesh(details, "S_a193_l1733", ar=250)),
            ("4_locate_D2450_a30", self.locate),
            ("5_timed_reextract_P_p80_h15", self.timed_reextract),
        ]
        try:
            job_queue._acquire_lock(lock)
        except Exception as exc:
            for name, _action in actions:
                self.record(name, "FAIL", {"error": str(exc)})
            return 1
        try:
            for name, action in actions:
                passed = self.step(name, action)
                if name == "1_cp_live_smoke":
                    self.cp_live_passed = passed
            if not velocity_smoke:
                text = followup_commands(self.root, self.passing_faces, self.cp_live_passed, self.stamp)
                print(text, flush=True)
                (self.attempt / "followup_commands.sh.txt").write_text(text, encoding="utf-8")
        finally:
            job_queue._release_lock(lock)
        return 2 if "FAIL" in self.results else 0


def followup_commands(root, passing_faces, cp_live_passed, stamp):
    queue_root = Path(root) / "queues"
    cp = [] if cp_live_passed else ["--no-cp-live"]
    lines = ["# Follow-ups only: none of these commands was executed.",
             "# Run sequentially after inspection. Never add --resume/--retry-failed."]

    def emit_and_run(command, path):
        lines.append(shlex.join(["python", *command]))
        lines.append(shlex.join(["python", "scripts/mfbo/job_queue.py", "run", str(path)]))

    for face in sorted(set(passing_faces), reverse=True):
        token = f"fs{round(face * 1000):03d}"
        path = queue_root / f"followup_{token}_{stamp}.json"
        lines.append(f"# Candidate {face:.3f} mm (requires a passing S_a193_l1733 check). Choose ONE; other geometries still need their mesh gates.")
        emit_and_run(["scripts/host/pending_checks.py", "--emit-finer-queue", str(path),
                      "--face-size", str(face), *cp], path)
        lines.append(f"CHOSEN_FACE_SIZE={face:.3f}  # Set only for the candidate you choose.")
    if not passing_faces:
        lines.append("# No passing finer-face candidate: do not emit that follow-up queue.")
    ar_path = queue_root / f"followup_ar250_{stamp}.json"
    emit_and_run(["scripts/campaign_v3.py", "--emit-queue", str(ar_path), "--run-id", "u0p2_p6M",
                  "--geo-id", "D1225_a30", "--geo-id", "S_a193_l1733", "--max-aspect-ratio", "250", *cp], ar_path)
    lines += ["# Velocity stage 1: bounded 0.05 m/s smoke on REF_empty; require PASS before proceeding.",
              shlex.join(["bash", "scripts/host/pending_checks.sh", "--velocity-smoke", *cp]),
              "# Velocity stage 2: 0.10 m/s x 31; the four failed geometries use the chosen finer face size.",
              ': "${CHOSEN_FACE_SIZE:?Choose a passing finer-face candidate first}"']
    speed_path = queue_root / f"velocity_u0p1_{stamp}.json"
    emit_and_run(["scripts/host/pending_checks.py", "--emit-velocity-queue", str(speed_path),
                  "--run-id", "u0p1_p6M", "--face-size", "CHOSEN_FACE_SIZE_TOKEN", *cp], speed_path)
    representatives = queue_root / f"velocity_representatives_{stamp}.json"
    flags = [value for geo in REPRESENTATIVES for value in ("--geo-id", geo)]
    lines.append("# Velocity stage 3: five representatives x 0.05/0.15/0.25 m/s (15 jobs).")
    emit_and_run(["scripts/host/pending_checks.py", "--emit-velocity-queue", str(representatives),
                  "--run-id", "u0p05_p6M", "--run-id", "u0p15_p6M", "--run-id", "u0p25_p6M",
                  "--face-size", "CHOSEN_FACE_SIZE_TOKEN", *flags, *cp], representatives)
    reextract_path = queue_root / f"reextract_pass_finals_{stamp}.json"
    lines.append("# Full re-extract of observed u0p2 PASS finals only; archives reports, preserves reports_iter*.")
    emit_and_run(["scripts/campaign_v3_reextract.py", "--emit-queue", str(reextract_path),
                  "--data-root", str(root)], reextract_path)
    return ("\n".join(lines) + "\n").replace("CHOSEN_FACE_SIZE_TOKEN", '"$CHOSEN_FACE_SIZE"')


def emit_finer_queue(path, face, no_cp_live, *, geo_ids=FAILED_GEOS, run_ids=("u0p2_p6M",)):
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"Refusing to replace queue: {destination}")
    spec = load_mesh_spec(default_spec_path())
    jobs = campaign.solve_jobs(spec, default_spec_path(), run_id_filter=run_ids,
                               geo_ids=geo_ids, no_cp_live=no_cp_live)
    sizing = json.dumps({"labels": ["wall_top_mem", "wall_bottom_mem"], "size_mm": face, "growth_rate": 1.2})
    for job in jobs:
        geo = job["argv"][job["argv"].index("--geo-id") + 1]
        if geo not in FAILED_GEOS:
            continue
        if "--local-face-size" in job["argv"]:
            job["argv"][job["argv"].index("--local-face-size") + 1] = sizing
        else:
            job["argv"].extend(["--local-face-size", sizing])
        job["id"] += f"-fs{round(face * 1000):03d}"
    campaign.write_queue(destination, jobs)
    print(f"Wrote {len(jobs)} finer-membrane mesh/solve jobs to {destination}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--velocity-smoke", action="store_true")
    modes.add_argument("--emit-finer-queue")
    modes.add_argument("--emit-velocity-queue")
    parser.add_argument("--run-id", action="append")
    parser.add_argument("--geo-id", action="append")
    parser.add_argument("--face-size", type=float, choices=(0.025, 0.020))
    parser.add_argument("--no-cp-live", action="store_true")
    args = parser.parse_args(argv)
    if args.no_cp_live and not (args.velocity_smoke or args.emit_finer_queue or args.emit_velocity_queue):
        parser.error("--no-cp-live is for follow-up emission/velocity smoke; check 1 always uses cp_live ON")
    if args.emit_finer_queue or args.emit_velocity_queue:
        if args.face_size is None:
            parser.error("--emit-finer-queue requires --face-size")
        if args.emit_velocity_queue:
            if not args.run_id:
                parser.error("--emit-velocity-queue requires --run-id")
            emit_finer_queue(args.emit_velocity_queue, args.face_size, args.no_cp_live,
                             geo_ids=args.geo_id, run_ids=args.run_id)
        else:
            emit_finer_queue(args.emit_finer_queue, args.face_size, args.no_cp_live)
        return 0
    if args.plan:
        print("Checks: REF_empty cp_live smoke; fs025/fs020; two ar250 meshes; D2450_a30 locate; timed P_p80_h15 full re-extract.")
        print("Conditional follow-up examples (candidate/CP success not yet established):")
        print(followup_commands(ROOT, [0.025, 0.020], False, "AFTER_CHECKS"))
        return 0
    if os.name != "nt":
        parser.error("Live checks require Git Bash and the Windows .venv; --plan is read-only anywhere.")
    if Path.cwd().resolve() != project_root().resolve():
        parser.error("Run from C:/pyfluent via scripts/host/pending_checks.sh")
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Interrupted; inspect the smoke recovery journal before another run.")
    signal.signal(signal.SIGTERM, interrupted)
    return Checks().run(velocity_smoke=args.velocity_smoke, no_cp_live=args.no_cp_live)


if __name__ == "__main__":
    raise SystemExit(main())
