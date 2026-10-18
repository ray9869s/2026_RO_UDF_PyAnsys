"""Pregenerated grid geometries. Nothing here launches Discovery."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_3d import (
    STATUS_VALID,
    Experiment3D,
    LiveEvaluator,
    PillarOutcome,
    PillarPoint,
    PregeneratedGeometryMissing,
    load_experiment,
    run_optimization,
)
from ro.mfbo_adapter import LF_MESH_SETTINGS

MESH = dict(LF_MESH_SETTINGS)
RUN_ID = "u0p2_p6M"
_FAKE_BATCH = """
import hashlib
import json
import os
import sys
from pathlib import Path


def arg(name):
    return sys.argv[sys.argv.index(name) + 1]


batch = Path(arg("--batch"))
log = Path(arg("--log"))
mode = arg("--mode")
entries = json.loads(batch.read_text(encoding="utf-8"))
log.parent.mkdir(parents=True, exist_ok=True)
previous = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
with log.open("a", encoding="utf-8") as handle:
    handle.write(str(os.getpid()) + chr(10))
    for entry in entries:
        handle.write("geo " + entry["geo_id"] + chr(10))
attempt = len([line for line in previous if line and not line.startswith("geo ")]) + 1


def write_success(entry):
    out = Path(entry["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    geo_id = entry["geo_id"]
    pmdb = out / (geo_id + ".pmdb")
    pmdb.write_bytes(b"pmdb-bytes")
    digest = hashlib.sha256(pmdb.read_bytes()).hexdigest()
    inputs = {
        "d_p_mm": entry["d_p_mm"],
        "d_h_mm": entry["d_h_mm"],
        "d_f_mm": entry["d_f_mm"],
        "geo_id": geo_id,
    }
    if int(entry["n_active"]) != 7:
        inputs["n_active"] = int(entry["n_active"])
    (out / (geo_id + "_meta.json")).write_text(
        json.dumps({"geo_id": geo_id, "inputs": inputs, "pmdb_sha256": digest}),
        encoding="utf-8",
    )
    (out / (geo_id + "_generate_wall_time.json")).write_text(
        json.dumps({"generate_wall_time_s": 1.25}) + chr(10),
        encoding="utf-8",
    )


if mode == "fail":
    raise SystemExit(1)
if mode == "retry" and attempt == 1:
    entry = entries[0]
    out = Path(entry["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / (entry["geo_id"] + ".pmdb")).write_bytes(b"partial")
    raise SystemExit(1)
if mode == "retry":
    entry = entries[0]
    partial = Path(entry["out_dir"]) / (entry["geo_id"] + ".pmdb")
    if partial.is_file() and partial.read_bytes() == b"partial":
        raise SystemExit(2)
for entry in entries:
    write_success(entry)
raise SystemExit(0)
"""


def test_v2_valid_grid_is_273_points_and_546_geometries(tmp_path: Path) -> None:
    script = _script()
    entries = script.geometries_for_grid(_v2_grid_config(), tmp_path)
    points = {(entry["d_p_mm"], entry["d_h_mm"]) for entry in entries}
    assert len(points) == 273
    assert len(entries) == 546
    assert len({entry["geo_id"] for entry in entries}) == 546
    hf = [entry for entry in entries if entry["n_active"] == 7]
    lf = [entry for entry in entries if entry["n_active"] == 3]
    assert len(hf) == 273
    assert len(lf) == 273
    assert all(not entry["geo_id"].endswith("_n3") for entry in hf)
    assert all("_n7" not in entry["geo_id"] for entry in hf)
    assert all(entry["geo_id"].endswith("_n3") for entry in lf)
    chunks = script.chunk_entries(entries, 40)
    assert [len(chunk) for chunk in chunks] == [40] * 13 + [26]
    payload = json.loads(
        (REPO_ROOT / "configs" / "mfbo_3d_experiment.v2.json").read_text(encoding="utf-8")
    )
    assert payload["geometry"] == "pregenerated"


def test_emit_queue_writes_one_job_per_chunk_without_launching(tmp_path: Path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("emit-queue must not start a process")

    monkeypatch.setattr(subprocess, "run", refuse)
    script = _script()
    config_path = _live_file(
        tmp_path,
        name="emit",
        grid=2,
        box=((0.75, 0.85), (0.12, 0.18)),
        points=((0.75, 0.12), (0.85, 0.18)),
        fidelity_table="configs/mfbo_fidelity_table.3d_v1.json",
    )
    queue = tmp_path / "pregenerate.json"
    code = script.main(
        [
            "emit-queue",
            "--config",
            str(config_path),
            "--data-root",
            str(tmp_path),
            "--out",
            str(queue),
            "--chunk-size",
            "3",
        ]
    )
    assert code == 0
    job_queue = load_module("job_queue_pregenerate", SCRIPTS_DIR / "mfbo" / "job_queue.py")
    jobs = job_queue.load_queue(queue)
    assert [job["id"] for job in jobs] == [
        "pilot-pregenerate-000",
        "pilot-pregenerate-001",
        "pilot-pregenerate-002",
    ]
    assert all(job["argv"][2] == "run-chunk" for job in jobs)
    assert all("--batch" in job["argv"] for job in jobs)
    assert all("generate_pillar_cad.py" not in " ".join(job["argv"]) for job in jobs)
    listed = []
    for job in jobs:
        listed.extend(json.loads(Path(job["argv"][-1]).read_text(encoding="utf-8")))
    assert [len(json.loads(Path(job["argv"][-1]).read_text(encoding="utf-8"))) for job in jobs] == [3, 3, 2]
    assert len(listed) == 8
    assert {entry["n_active"] for entry in listed} == {3, 7}
    assert len({entry["geo_id"] for entry in listed}) == 8


def test_failed_chunk_retries_once_in_a_new_process_and_skips_passed(tmp_path: Path, capsys) -> None:
    script = _script()
    entries = _two_entries(tmp_path)
    _write_passed(entries[0])
    batch = tmp_path / "chunk.json"
    batch.write_text(json.dumps(entries), encoding="utf-8")
    log = tmp_path / "retry.log"
    code = script.execute_chunk(batch, command=_command(tmp_path, log, "retry"))
    assert code == 0
    pids = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("geo ")
    ]
    assert len(pids) == 2
    assert pids[0] != pids[1]
    asked = [line.removeprefix("geo ") for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("geo ")]
    assert entries[0]["geo_id"] not in asked
    assert entries[1]["geo_id"] in asked
    assert script.geometry_passed(entries[0])
    assert script.geometry_passed(entries[1])
    assert "skipped=1 generated=1 failed=0 attempts=2" in capsys.readouterr().out

    again = tmp_path / "again.log"
    calls: list[Path] = []

    def command(path: Path) -> list[str]:
        calls.append(path)
        return _command(tmp_path, again, "ok")(path)

    assert script.execute_chunk(batch, command=command) == 0
    assert calls == []


def test_a_chunk_that_fails_twice_is_reported(tmp_path: Path, capsys) -> None:
    script = _script()
    entries = _two_entries(tmp_path)
    batch = tmp_path / "chunk.json"
    batch.write_text(json.dumps(entries), encoding="utf-8")
    log = tmp_path / "fail.log"
    code = script.execute_chunk(batch, command=_command(tmp_path, log, "fail"))
    assert code == 1
    pids = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("geo ")
    ]
    assert len(pids) == 2
    assert pids[0] != pids[1]
    assert "failed=2 attempts=2" in capsys.readouterr().out


def test_pregenerated_resumes_a_history_that_did_not_set_geometry(tmp_path: Path) -> None:
    path = _offline_file(tmp_path)
    config = load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)
    assert config.geometry == "generate"
    assert "geometry" not in config.problem
    destination = tmp_path / "hf"
    run_optimization(config, config.replay, destination)
    stored = json.loads((destination / "config.json").read_text(encoding="utf-8"))
    assert "geometry" not in stored["problem"]
    before = json.loads((destination / "history.json").read_text(encoding="utf-8"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["geometry"] = "pregenerated"
    path.write_text(json.dumps(payload), encoding="utf-8")
    resumed = load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)
    assert resumed.geometry == "pregenerated"
    assert resumed.problem == config.problem
    run_optimization(resumed, resumed.replay, destination)
    after = json.loads((destination / "history.json").read_text(encoding="utf-8"))
    assert after == before

    payload["seed"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    changed = load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)
    with pytest.raises(ValueError, match="experiment changed"):
        run_optimization(changed, changed.replay, destination)


def test_missing_pregenerated_geometry_stops_the_run(tmp_path: Path) -> None:
    config = _live(tmp_path, name="missing")
    drivers = _Drivers()
    evaluator = LiveEvaluator(
        tmp_path,
        config.fidelity_settings,
        config.run_id,
        config.d_f_mm,
        drivers,
        geometry="pregenerated",
    )
    with pytest.raises(PregeneratedGeometryMissing):
        run_optimization(config, evaluator, tmp_path / "run")
    assert drivers.generate_calls == 0


def test_pregenerated_failure_charges_the_recorded_generate_time(tmp_path: Path) -> None:
    box = ((0.75, 0.85), (0.12, 0.18))
    config = _live(
        tmp_path,
        name="charge",
        points=((0.75, 0.12),),
        grid=2,
        box=box,
    )
    for d_p in box[0]:
        for d_h in box[1]:
            _write_passed(_entry(tmp_path, d_p, d_h, n_active=7), seconds=7.25)
    drivers = _Drivers()
    evaluator = LiveEvaluator(
        tmp_path,
        config.fidelity_settings,
        config.run_id,
        config.d_f_mm,
        drivers,
        geometry="pregenerated",
    )
    destination = tmp_path / "charge_run"
    run_optimization(config, evaluator, destination)
    assert drivers.generate_calls == 0
    history = json.loads((destination / "history.json").read_text(encoding="utf-8"))
    assert history
    assert all(row["generate_wall_time_s"] == pytest.approx(7.25) for row in history)
    assert all(row["charged_wall_s"] == pytest.approx(7.25) for row in history)


def test_a_valid_run_records_generate_time_and_charges_the_three_stages(tmp_path: Path) -> None:
    config = _live(tmp_path, name="validcharge", points=((0.8, 0.15),))

    def evaluator(design, fidelity):
        return PillarOutcome(
            d_p_mm=design.d_p_mm,
            d_h_mm=design.d_h_mm,
            fidelity=fidelity,
            status=STATUS_VALID,
            lmh=20.0,
            cp_average=1.01,
            dp_per_l=1000.0,
            generate_wall_time_s=7.25,
            mesh_wall_time_s=2.0,
            solver_wall_time_s=3.0,
            extraction_wall_time_s=4.0,
        )

    destination = tmp_path / "valid_run"
    run_optimization(config, evaluator, destination)
    history = json.loads((destination / "history.json").read_text(encoding="utf-8"))
    assert history[0]["generate_wall_time_s"] == pytest.approx(7.25)
    assert history[0]["charged_wall_s"] == pytest.approx(9.0)


def test_pregenerated_run_does_not_start_discovery(tmp_path: Path, monkeypatch, capsys) -> None:
    calls: list[object] = []

    def spy(*args, **_kwargs):
        calls.append(args)
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", spy)
    script = load_module("run_3d_pregenerated", SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py")
    code = script.main(
        [
            "run",
            "--config",
            str(_live_file(tmp_path, name="nodisc", geometry="pregenerated")),
            "--data-root",
            str(tmp_path),
            "--method",
            "hf_bo",
        ]
    )
    assert code == 1
    assert calls == []
    assert "pregenerated geometry" in capsys.readouterr().err


def test_pregenerated_refuses_the_geometry_worker(tmp_path: Path, capsys, monkeypatch) -> None:
    opened: list[str] = []
    import ro.geometry_worker as geometry_worker

    monkeypatch.setattr(
        geometry_worker,
        "open_geometry_worker",
        lambda *_args, **_kwargs: opened.append("opened"),
    )
    script = load_module("run_3d_pregenerated_worker", SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py")
    code = script.main(
        [
            "run",
            "--config",
            str(_live_file(tmp_path, name="worker", geometry="pregenerated")),
            "--data-root",
            str(tmp_path),
            "--method",
            "hf_bo",
            "--geometry-worker",
        ]
    )
    assert code == 1
    assert opened == []
    assert "Omit --geometry-worker" in capsys.readouterr().err


def test_optimization_queue_omits_the_geometry_worker_when_pregenerated(tmp_path: Path) -> None:
    script = load_module("run_3d_pregenerated_emit", SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py")
    queue = tmp_path / "opt.json"
    code = script.main(
        [
            "emit-queue",
            "--config",
            str(_live_file(tmp_path, name="optq", geometry="pregenerated")),
            "--data-root",
            str(tmp_path),
            "--out",
            str(queue),
        ]
    )
    assert code == 0
    jobs = json.loads(queue.read_text(encoding="utf-8"))
    assert [job["id"] for job in jobs] == ["pilot-hf_bo", "pilot-mfbo"]
    assert all("--geometry-worker" not in job["argv"] for job in jobs)


def test_batch_command_is_a_new_interpreter() -> None:
    argv = _script().default_batch_command(Path("chunk.json"))
    assert argv[0] == sys.executable
    assert argv[1].endswith("generate_pillar_cad.py")
    assert argv[2:] == ["--batch", "chunk.json"]


def test_batch_process_gets_a_console_and_not_the_queue_log(tmp_path, monkeypatch) -> None:
    script = _script()
    if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        assert script._CREATE_NEW_CONSOLE == subprocess.CREATE_NEW_CONSOLE
        assert script._CREATE_NO_WINDOW == subprocess.CREATE_NO_WINDOW
        assert script._DETACHED_PROCESS == subprocess.DETACHED_PROCESS
        assert script._CREATE_NEW_PROCESS_GROUP == subprocess.CREATE_NEW_PROCESS_GROUP
    flags = script.discovery_launch_flags("nt")
    assert flags & script._CREATE_NEW_CONSOLE
    assert flags & script._CREATE_NO_WINDOW == 0
    assert flags & script._DETACHED_PROCESS == 0
    assert flags & script._CREATE_NEW_PROCESS_GROUP == 0
    assert script.discovery_launch_flags("posix") == 0
    kwargs = script.batch_subprocess_kwargs()
    assert kwargs["stdin"] is None
    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None
    if os.name == "nt":
        assert kwargs["creationflags"] == flags
    else:
        assert "creationflags" not in kwargs

    entries = _two_entries(tmp_path)
    batch = tmp_path / "chunk.json"
    batch.write_text(json.dumps(entries), encoding="utf-8")
    seen: dict = {}

    def fake_run(argv, **run_kwargs):
        seen["argv"] = list(argv)
        seen["kwargs"] = run_kwargs
        pending = json.loads(Path(argv[argv.index("--batch") + 1]).read_text(encoding="utf-8"))
        for entry in pending:
            _write_passed(entry)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    assert script.execute_chunk(batch) == 0
    assert seen["argv"][0] == sys.executable
    assert seen["argv"][1].endswith("generate_pillar_cad.py")
    assert seen["argv"][2:4] == ["--batch", str(batch.with_suffix(".pending.json"))]
    assert seen["kwargs"] == kwargs
    assert "env" not in seen["kwargs"]
    assert "cwd" not in seen["kwargs"]


def test_unknown_geometry_is_refused(tmp_path: Path) -> None:
    path = _live_file(tmp_path, name="badgeo", geometry="cached")
    with pytest.raises(ValueError, match="geometry"):
        load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)


class _Drivers:
    def __init__(self) -> None:
        self.generate_calls = 0

    def generate(self, **_kwargs) -> None:
        self.generate_calls += 1
        raise AssertionError("Discovery must not start")

    def mesh(self, **_kwargs) -> None:
        raise DriverFailed("mesh refused")

    def solve(self, **_kwargs) -> None:
        raise AssertionError("solve")

    def extract(self, **_kwargs) -> None:
        raise AssertionError("extract")


class DriverFailed(RuntimeError):
    pass


def _script():
    return load_module("pregenerate_grid_under_test", SCRIPTS_DIR / "mfbo" / "pregenerate_grid.py")


def _v2_grid_config() -> Experiment3D:
    payload = json.loads(
        (REPO_ROOT / "configs" / "mfbo_3d_experiment.v2.json").read_text(encoding="utf-8")
    )
    table = json.loads(
        (REPO_ROOT / "configs" / "mfbo_fidelity_table.3d_v1.json").read_text(encoding="utf-8")
    )
    box = payload["design_box"]
    return Experiment3D(
        method="mfbo",
        mode="live",
        experiment_id="v2",
        seed=0,
        run_id=RUN_ID,
        d_f_mm=0.4,
        d_p_min_mm=box["d_p_mm"][0],
        d_p_max_mm=box["d_p_mm"][1],
        d_h_min_mm=box["d_h_mm"][0],
        d_h_max_mm=box["d_h_mm"][1],
        cp_excess_max=0.04,
        dp_max_pa_per_m=72000.0,
        budget_hf_equivalent=1.0,
        max_sequential=0,
        reference_hf_wall_s=100.0,
        lf_over_hf_cost_ratio=0.54,
        candidate_grid=17,
        initial_hf=tuple(PillarPoint(item["d_p_mm"], item["d_h_mm"]) for item in payload["initial_hf"]),
        initial_lf=tuple(PillarPoint(item["d_p_mm"], item["d_h_mm"]) for item in payload["initial_lf"]),
        fidelity_settings={"LF": table["LF_d"], "HF": table["HF_m"]},
        replay=None,
        problem={},
    )


def _live(
    tmp_path: Path,
    *,
    name: str,
    points=((0.8, 0.15), (0.6, 0.3)),
    geometry="pregenerated",
    grid=3,
    box=((0.6, 1.0), (0.0, 0.3)),
):
    path = _live_file(
        tmp_path,
        name=name,
        points=points,
        geometry=geometry,
        grid=grid,
        box=box,
    )
    return load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)


def _live_file(
    tmp_path: Path,
    *,
    name: str,
    points=((0.8, 0.15), (0.6, 0.3)),
    geometry="generate",
    grid=3,
    box=((0.6, 1.0), (0.0, 0.3)),
    fidelity_table: str | None = None,
) -> Path:
    fidelity = tmp_path / f"{name}_fidelity.json"
    if fidelity_table is None:
        fidelity.write_text(json.dumps({"LF": MESH, "HF": MESH}), encoding="utf-8")
        fidelity_table = str(fidelity)
    payload = {
        "experiment_id": "pilot",
        "method": "hf_bo",
        "mode": "live",
        "seed": 0,
        "run_id": RUN_ID,
        "d_f_mm": 0.4,
        "design_box": {"d_p_mm": list(box[0]), "d_h_mm": list(box[1])},
        "cp_excess_max": 0.05,
        "dp_max_pa_per_m": 20000.0,
        "budget_hf_equivalent": 8.0,
        "max_sequential": 0,
        "reference_hf_wall_s": 100.0,
        "lf_over_hf_cost_ratio": 0.25,
        "candidate_grid": grid,
        "initial_hf": [{"d_p_mm": d_p, "d_h_mm": d_h} for d_p, d_h in points],
        "initial_lf": [{"d_p_mm": d_p, "d_h_mm": d_h} for d_p, d_h in points],
        "fidelity_table": fidelity_table,
        "replay_table": None,
    }
    if geometry != "generate":
        payload["geometry"] = geometry
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _offline_file(tmp_path: Path) -> Path:
    fidelity = tmp_path / "offline_fidelity.json"
    replay = tmp_path / "offline_replay.json"
    fidelity.write_text(json.dumps({"LF": MESH, "HF": MESH}), encoding="utf-8")
    replay.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "d_p_mm": 0.8,
                        "d_h_mm": 0.15,
                        "d_f_mm": 0.4,
                        "fidelity": "HF",
                        "status": "valid",
                        "failure_reason": None,
                        "lmh": 20.0,
                        "cp_average": 1.01,
                        "pressure_drop_per_length_pa_per_m": 1000.0,
                        "mesh_wall_time_s": 2.0,
                        "solver_wall_time_s": 3.0,
                        "extraction_wall_time_s": 4.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "experiment_id": "pilot",
        "method": "hf_bo",
        "mode": "offline_replay",
        "seed": 0,
        "run_id": RUN_ID,
        "d_f_mm": 0.4,
        "design_box": {"d_p_mm": [0.6, 1.0], "d_h_mm": [0.0, 0.3]},
        "cp_excess_max": 0.05,
        "dp_max_pa_per_m": 20000.0,
        "budget_hf_equivalent": 8.0,
        "max_sequential": 0,
        "reference_hf_wall_s": 100.0,
        "lf_over_hf_cost_ratio": 0.25,
        "candidate_grid": 3,
        "initial_hf": [{"d_p_mm": 0.8, "d_h_mm": 0.15}],
        "initial_lf": [],
        "fidelity_table": str(fidelity),
        "replay_table": str(replay),
    }
    path = tmp_path / "offline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _two_entries(tmp_path: Path) -> list[dict]:
    return [
        _entry(tmp_path, 0.8, 0.15, n_active=7),
        _entry(tmp_path, 0.8, 0.15, n_active=3),
    ]


def _entry(tmp_path: Path, d_p: float, d_h: float, *, n_active: int) -> dict:
    geo_id = format_mfbo_pillar_geo_id(d_p, d_h, 0.4, n_active=n_active)
    return {
        "d_p_mm": d_p,
        "d_h_mm": d_h,
        "d_f_mm": 0.4,
        "geo_id": geo_id,
        "out_dir": (tmp_path / "geometries" / "pillar" / geo_id).as_posix(),
        "n_active": n_active,
    }


def _write_passed(entry: dict, *, seconds: float = 1.25) -> None:
    out = Path(entry["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    geo_id = entry["geo_id"]
    pmdb = out / f"{geo_id}.pmdb"
    pmdb.write_bytes(b"pmdb-bytes")
    digest = hashlib.sha256(pmdb.read_bytes()).hexdigest()
    inputs = {
        "d_p_mm": entry["d_p_mm"],
        "d_h_mm": entry["d_h_mm"],
        "d_f_mm": entry["d_f_mm"],
        "geo_id": geo_id,
    }
    if int(entry["n_active"]) != 7:
        inputs["n_active"] = int(entry["n_active"])
    (out / f"{geo_id}_meta.json").write_text(
        json.dumps({"geo_id": geo_id, "inputs": inputs, "pmdb_sha256": digest}),
        encoding="utf-8",
    )
    (out / f"{geo_id}_generate_wall_time.json").write_text(
        json.dumps({"generate_wall_time_s": seconds}) + "\n",
        encoding="utf-8",
    )


def _command(tmp_path: Path, log: Path, mode: str):
    fake = tmp_path / f"fake_{mode}.py"
    if not fake.is_file():
        fake.write_text(_FAKE_BATCH, encoding="utf-8")

    def command(batch: Path) -> list[str]:
        return [
            sys.executable,
            str(fake),
            "--batch",
            str(batch),
            "--log",
            str(log),
            "--mode",
            mode,
        ]

    return command
