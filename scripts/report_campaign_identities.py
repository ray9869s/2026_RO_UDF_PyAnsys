#!/usr/bin/env python3
"""Report R-11 campaign identities on existing mesh/run leaves. No Fluent.

Missing optional fields (null G, null u_mean_ms, missing msh for SHA) are
N/A, not rejects. Does not add schema required fields. WSL without
RO_DATA_ROOT cannot scan C:/ro_data; run this on the workstation.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ro.manifest import (
    ManifestError,
    iter_run_manifests,
    read_mesh_manifest,
)
from ro.manifest_validation import (
    campaign_blocked_frac_block_reason,
    mesh_run_blocked_frac_block_reason,
)
from ro.paths import data_root, mesh_dir, meshes_root, runs_root
from ro.solver_common import (
    mesh_sha256_file_block_reason,
    run_id_operating_point_block_reason,
    u_mean_profile_identity_block_reason,
)


def _mesh_file(payload) -> Path:
    directory = mesh_dir(payload["family"], payload["geo_id"], payload["mesh_id"])
    return directory / f"{payload['geo_id']}_{payload['mesh_id']}.msh.h5"


def _label(reason, *, applies: bool = True) -> str:
    if not applies:
        return "N/A"
    return "PASS" if reason is None else "REJECT"


def _iter_mesh_payloads():
    root = meshes_root()
    if not root.is_dir():
        raise NotADirectoryError(f"meshes_root is not a directory: {root}")
    for manifest_path in sorted(root.rglob("manifest.json")):
        try:
            yield manifest_path, read_mesh_manifest(manifest_path.parent), None
        except (OSError, ManifestError) as exc:
            yield manifest_path, None, str(exc)


def report_mesh_leaf(manifest_path: Path, payload, error, *, file=None):
    file = file or sys.stdout
    if error is not None:
        print(f"mesh  UNREADABLE  {manifest_path}: {error}", file=file)
        return {"kind": "mesh", "unreadable": True}
    leaf = f"{payload['family']}/{payload['geo_id']}/{payload['mesh_id']}"
    blocked_applies = "membrane_blocked_area_frac" in payload
    blocked = campaign_blocked_frac_block_reason(payload)
    mesh_file = _mesh_file(payload)
    sha_applies = mesh_file.is_file()
    sha = mesh_sha256_file_block_reason(payload.get("mesh_sha256"), mesh_file)
    extra = ""
    if blocked_applies and blocked:
        extra += f"  blocked_reason={blocked}"
    if sha_applies and sha:
        extra += f"  sha_reason={sha}"
    print(
        f"mesh  blocked={_label(blocked, applies=blocked_applies)}  "
        f"sha={_label(sha, applies=sha_applies)}  {leaf}{extra}",
        file=file,
    )
    return {
        "kind": "mesh",
        "leaf": leaf,
        "blocked": blocked if blocked_applies else None,
        "sha": sha if sha_applies else None,
        "unreadable": False,
    }


def report_run_leaf(manifest_path: Path, payload, *, file=None):
    file = file or sys.stdout
    leaf = (
        f"{payload['family']}/{payload['geo_id']}/"
        f"{payload['mesh_id']}/{payload['run_id']}"
    )
    blocked_applies = "membrane_blocked_area_frac" in payload
    blocked = campaign_blocked_frac_block_reason(payload)
    run_id = run_id_operating_point_block_reason(
        payload["run_id"],
        payload["u_target_ms"],
        payload["p_gauge_pa"],
    )
    mesh_payload = None
    mesh_error = None
    try:
        mesh_payload = read_mesh_manifest(
            mesh_dir(payload["family"], payload["geo_id"], payload["mesh_id"])
        )
    except (OSError, ManifestError) as exc:
        mesh_error = str(exc)
    agree_applies = (
        mesh_payload is not None
        and "membrane_blocked_area_frac" in mesh_payload
        and "membrane_blocked_area_frac" in payload
    )
    agree = (
        mesh_run_blocked_frac_block_reason(mesh_payload, payload)
        if agree_applies
        else None
    )
    g_value = None if mesh_payload is None else mesh_payload.get("inlet_profile_G")
    ug_applies = (
        payload.get("inlet_bc_type") == "parabolic"
        and payload.get("u_mean_ms") is not None
        and g_value is not None
    )
    ug = u_mean_profile_identity_block_reason(
        payload.get("u_mean_ms"),
        g_value,
        payload["u_target_ms"],
        inlet_bc_type=payload.get("inlet_bc_type", "parabolic"),
    )
    mesh_file = _mesh_file(payload)
    sha_applies = mesh_file.is_file()
    sha = mesh_sha256_file_block_reason(payload.get("mesh_sha256"), mesh_file)
    extra = ""
    if blocked_applies and blocked:
        extra += f"  blocked_reason={blocked}"
    if agree_applies and agree:
        extra += f"  agree_reason={agree}"
    elif mesh_error is not None:
        extra += f"  mesh_unreadable={mesh_error}"
    if run_id:
        extra += f"  run_id_reason={run_id}"
    if sha_applies and sha:
        extra += f"  sha_reason={sha}"
    if ug_applies and ug:
        extra += f"  uG_reason={ug}"
    print(
        f"run   blocked={_label(blocked, applies=blocked_applies)}  "
        f"agree={_label(agree, applies=agree_applies)}  "
        f"run_id={_label(run_id)}  "
        f"sha={_label(sha, applies=sha_applies)}  "
        f"uG={_label(ug, applies=ug_applies)}  {leaf}{extra}",
        file=file,
    )
    return {
        "kind": "run",
        "leaf": leaf,
        "blocked": blocked if blocked_applies else None,
        "agree": agree if agree_applies else None,
        "run_id": run_id,
        "sha": sha if sha_applies else None,
        "uG": ug if ug_applies else None,
        "unreadable": False,
    }


def _reject_n(rows, key):
    return sum(1 for row in rows if row.get(key))


def main(argv=None) -> int:
    del argv
    data_root()
    print("CAMPAIGN IDENTITY REPORT (no Fluent)")
    print(f"meshes_root={meshes_root()}")
    print(f"runs_root={runs_root()}")
    mesh_rows = [
        report_mesh_leaf(manifest_path, payload, error)
        for manifest_path, payload, error in _iter_mesh_payloads()
    ]
    skipped_runs: list[tuple[Path, str]] = []
    run_rows = []
    if not runs_root().is_dir():
        raise NotADirectoryError(f"runs_root is not a directory: {runs_root()}")
    for manifest_path, payload in iter_run_manifests(
        skip_invalid=True,
        skipped_manifests=skipped_runs,
    ):
        run_rows.append(report_run_leaf(manifest_path, payload))
    for manifest_path, error in skipped_runs:
        print(f"run   UNREADABLE  {manifest_path}: {error}")
        run_rows.append({"kind": "run", "unreadable": True})

    mesh_unread = sum(1 for row in mesh_rows if row.get("unreadable"))
    run_unread = sum(1 for row in run_rows if row.get("unreadable"))
    print(
        "Summary: "
        f"mesh {len(mesh_rows)} unread={mesh_unread} "
        f"(blocked_reject={_reject_n(mesh_rows, 'blocked')} "
        f"sha_reject={_reject_n(mesh_rows, 'sha')}); "
        f"run {len(run_rows)} unread={run_unread} "
        f"(blocked_reject={_reject_n(run_rows, 'blocked')} "
        f"agree_reject={_reject_n(run_rows, 'agree')} "
        f"run_id_reject={_reject_n(run_rows, 'run_id')} "
        f"sha_reject={_reject_n(run_rows, 'sha')} "
        f"uG_reject={_reject_n(run_rows, 'uG')})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
