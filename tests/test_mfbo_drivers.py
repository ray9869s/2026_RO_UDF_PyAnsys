"""Subprocess drivers. The runner is a fake; nothing here launches Fluent."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ro.mfbo_adapter import PillarDesign
from ro.mfbo_drivers import DriverFailed, make_drivers, repo_root

DESIGN = PillarDesign(d_p_mm=0.8, d_h_mm=0.0, d_f_mm=0.4)
GEO_ID = "MFP_d0800_h0000_f0400"
MESH_ID = "max085_min006_cpg5_bl4_peel2"
RUN_ID = "u0p2_p6M"
SETTINGS = {
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 4,
    "peel_layers": 2,
}


def _ok_runner(calls):
    def runner(argv, **kwargs):
        calls.append({"argv": argv, "env": kwargs["env"], "shell": kwargs["shell"]})
        stdout = kwargs.get("stdout")
        if stdout is not None:
            stdout.write(b"ok\n")
        return SimpleNamespace(returncode=0)

    return runner


def test_child_gets_data_root_and_parent_env_stays(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", "C:/keep-me")
    calls = []
    drivers = make_drivers(tmp_path, runner=_ok_runner(calls))
    out_dir = tmp_path / "geometries" / "pillar" / GEO_ID
    drivers.generate(design=DESIGN, geo_id=GEO_ID, out_dir=out_dir)
    assert os.environ["RO_DATA_ROOT"] == "C:/keep-me"
    assert calls[0]["env"]["RO_DATA_ROOT"] == tmp_path.resolve().as_posix()
    assert calls[0]["shell"] is False
    argv = calls[0]["argv"]
    assert argv[1].endswith("scripts/generate_pillar_cad.py")
    assert "--d-p-mm" in argv
    assert "--out-dir" in argv
    assert str(out_dir) in argv
    assert calls[0]["env"] is not os.environ


def test_mesh_argv_passes_fidelity_settings_and_solve_does_not(tmp_path):
    calls = []
    drivers = make_drivers(tmp_path, runner=_ok_runner(calls))
    drivers.mesh(
        design=DESIGN,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        mesh_settings=SETTINGS,
        mesh_dir=tmp_path / "mesh",
    )
    mesh_argv = calls[0]["argv"]
    assert mesh_argv[1].endswith("scripts/mfbo/mesh_mfbo_case.py")
    assert mesh_argv[mesh_argv.index("--mesh-id") + 1] == MESH_ID
    assert mesh_argv[mesh_argv.index("--m-max") + 1] == "0.085"
    assert mesh_argv[mesh_argv.index("--m-min") + 1] == "0.006"
    assert mesh_argv[mesh_argv.index("--m-cpg") + 1] == "5"
    assert mesh_argv[mesh_argv.index("--bl-layers") + 1] == "4"
    assert mesh_argv[mesh_argv.index("--peel-layers") + 1] == "2"
    assert "--spacer-bl-layers" not in mesh_argv
    assert "--geometry-root" not in mesh_argv
    assert ".pmdb" in drivers.mesh.__doc__
    assert "geometry_root" in drivers.mesh.__doc__

    with_spacer = dict(SETTINGS)
    with_spacer["spacer_bl_layers"] = 6
    drivers.mesh(
        design=DESIGN,
        geo_id=GEO_ID,
        mesh_id="max085_min006_cpg5_bl4s6_peel2",
        mesh_settings=with_spacer,
        mesh_dir=tmp_path / "mesh2",
    )
    spacer_argv = calls[1]["argv"]
    assert spacer_argv[spacer_argv.index("--spacer-bl-layers") + 1] == "6"

    run_dir = tmp_path / "runs" / "pillar" / GEO_ID / MESH_ID / RUN_ID
    drivers.solve(
        design=DESIGN,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        run_dir=run_dir,
    )
    solve_argv = calls[2]["argv"]
    assert solve_argv[1].endswith("scripts/mfbo/solve_mfbo_case.py")
    assert solve_argv[solve_argv.index("--case") + 1] == RUN_ID
    assert "--m-max" not in solve_argv

    drivers.extract(
        design=DESIGN,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        run_dir=run_dir,
    )
    extract_argv = calls[3]["argv"]
    assert extract_argv[1].endswith("scripts/mfbo/reextract_runs.py")
    assert extract_argv[-1] == str(run_dir)
    assert Path(calls[0]["argv"][1]).is_file()


def test_runner_cwd_is_the_repo(tmp_path):
    seen = {}

    def runner(argv, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0)

    drivers = make_drivers(tmp_path, runner=runner)
    drivers.generate(
        design=DESIGN,
        geo_id=GEO_ID,
        out_dir=tmp_path / "geo",
    )
    assert seen["cwd"] == str(repo_root())


def test_nonzero_exit_raises_with_the_child_log(tmp_path):
    def runner(argv, **kwargs):
        kwargs["stdout"].write(b"mesh failed\n")
        return SimpleNamespace(returncode=7)

    drivers = make_drivers(tmp_path, runner=runner)
    with pytest.raises(DriverFailed, match="Child log:") as caught:
        drivers.mesh(
            design=DESIGN,
            geo_id=GEO_ID,
            mesh_id=MESH_ID,
            mesh_settings=SETTINGS,
            mesh_dir=tmp_path / "mesh",
        )
    assert caught.value.returncode == 7
    assert caught.value.stage == "mesh"
    assert caught.value.log_path.is_file()
    assert b"mesh failed" in caught.value.log_path.read_bytes()
    assert str(caught.value.log_path) in str(caught.value)
    assert not (tmp_path / "driver_logs" / "job.lock").exists()


def test_startup_error_names_the_log(tmp_path):
    def runner(argv, **kwargs):
        raise OSError("no such executable")

    drivers = make_drivers(tmp_path, runner=runner)
    with pytest.raises(DriverFailed, match="did not start") as caught:
        drivers.solve(
            design=DESIGN,
            geo_id=GEO_ID,
            mesh_id=MESH_ID,
            run_id=RUN_ID,
            run_dir=tmp_path / "run",
        )
    assert caught.value.returncode is None
    text = caught.value.log_path.read_text(encoding="utf-8")
    assert "no such executable" in text


def test_one_fluent_job_at_a_time(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    state = {"active": 0, "max": 0, "calls": 0}

    def runner(argv, **kwargs):
        state["active"] += 1
        state["calls"] += 1
        state["max"] = max(state["max"], state["active"])
        if state["calls"] == 1:
            entered.set()
            assert release.wait(2.0)
        state["active"] -= 1
        return SimpleNamespace(returncode=0)

    drivers = make_drivers(tmp_path, runner=runner)

    def _call():
        drivers.generate(design=DESIGN, geo_id=GEO_ID, out_dir=tmp_path / "geo")

    first = threading.Thread(target=_call)
    second = threading.Thread(target=_call)
    first.start()
    second.start()
    assert entered.wait(2.0)
    assert state["active"] == 1
    assert state["max"] == 1
    release.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    assert state["calls"] == 2
    assert state["max"] == 1
    assert not first.is_alive()
    assert not second.is_alive()
