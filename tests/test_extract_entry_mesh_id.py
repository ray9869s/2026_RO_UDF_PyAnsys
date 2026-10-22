"""Extract entry resolves mesh_id without reading it from post_config."""

from __future__ import annotations

import json
import runpy
import sys

import pytest

from helpers import CONFIGS_DIR, REPO_ROOT, SCRIPTS_DIR, load_module, load_post_config
from ro.manifest import write_run_manifest
from ro.paths import run_dir
from test_manifest import FAMILY, GEO_ID, RUN_ID, run_payload

EXTRACT_PATH = SCRIPTS_DIR / "pyfluent_report_extract.py"
MESH_ID = "max085_min006_cpg5_bl4_peel2_y1h020"


class ExtractReachedFluent(RuntimeError):
    """Sentinel raised in place of a real Fluent launch."""


def test_mesh_id_for_extract_uses_the_leaf_when_the_manifest_omits_it(tmp_path):
    extract = load_module("report_extract_mesh_id_helper", EXTRACT_PATH)
    leaf = (
        tmp_path
        / "runs"
        / "diamond"
        / "D2450_a45"
        / "max085_min006_cpg5_bl4_peel2_y1h014"
        / "u0p2_p6M"
    )
    assert extract.mesh_id_for_extract({}, leaf) == (
        "max085_min006_cpg5_bl4_peel2_y1h014"
    )
    assert extract.mesh_id_for_extract(
        {"mesh_id": MESH_ID},
        leaf,
    ) == MESH_ID


def test_extract_entry_path_does_not_read_mesh_id_from_post_config(
    monkeypatch, tmp_path, capsys
):
    cfg = load_post_config()
    assert not hasattr(cfg, "mesh_id")

    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path.resolve()))
    monkeypatch.setenv("PYFLUENT_PROJECT_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("PYFLUENT_POST_CONFIG", str(CONFIGS_DIR / "post_config.py"))
    monkeypatch.setattr(sys, "argv", ["pyfluent_report_extract.py"])

    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    payload = run_payload()
    payload["mesh_id"] = MESH_ID
    payload["u_mean_source_mesh_id"] = MESH_ID
    write_run_manifest(run_directory, payload)
    (run_directory / "260822_RO_UDF.c").write_text(
        "#ifndef RO_ANALYTIC_CWALL\n#define RO_ANALYTIC_CWALL 1\n#endif\n",
        encoding="utf-8",
    )
    (run_directory / "fluent-y1.trn").write_text(
        "y1 area-weighted mean = 5.0 um\n",
        encoding="utf-8",
    )
    (run_directory / f"{GEO_ID}_{RUN_ID}_final.cas.h5").write_bytes(b"cas")
    (run_directory / f"{GEO_ID}_{RUN_ID}_final.dat.h5").write_bytes(b"dat")
    monkeypatch.setenv(
        "PYFLUENT_POST_OVERRIDES",
        json.dumps(
            {
                "geo_name": GEO_ID,
                "case_name": RUN_ID,
                "case_path": str(run_directory),
                "n_buffer_in": 1,
                "n_active": 7,
                "n_buffer_out": 3,
                "cell_length_x_m": 0.003465,
                "buffer_length_in_m": 0.003465,
                "buffer_length_out_m": 0.00693,
            }
        ),
    )

    import ansys.fluent.core as pyfluent

    def _stop(*_args, **_kwargs):
        raise ExtractReachedFluent("launch_fluent")

    monkeypatch.setattr(pyfluent, "launch_fluent", _stop)
    saved_main = sys.modules.get("__main__")
    try:
        with pytest.raises(ExtractReachedFluent):
            runpy.run_path(str(EXTRACT_PATH), run_name="__main__")
    finally:
        if saved_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = saved_main

    output = capsys.readouterr().out
    assert f"extract mesh_id: {MESH_ID}" in output
    assert f"run_mesh_id={MESH_ID}" in output
    assert "FLAG y1 request: realised 5.0 um vs requested 2.0 um" in output
    assert "has no attribute 'mesh_id'" not in output
    assert output.index(f"extract mesh_id: {MESH_ID}") < output.index(
        "Launching Fluent"
    )
