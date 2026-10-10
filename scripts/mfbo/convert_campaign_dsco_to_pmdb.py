"""Convert the 31 campaign .dsco files to .pmdb with Discovery 25.1.

One geometry per Discovery session, same launch as ``pillar_cad``. Writes
``<family>/<geo_id>/<geo_id>.pmdb``, ``.scdocx``, and ``_meta.json`` under
the output root (default ``C:/ro_data_mfbo/geometries_pmdb``). Never writes
into ``C:/ro_data``. A failed geometry is recorded and the loop continues.

Importing this module does not launch Discovery. ``--emit-queue`` writes a
job-queue JSON file and does not convert.

Production meshing stays on ``.dsco`` under ``RO_DATA_ROOT/geometries``.
To mesh from this tree, leave those defaults and pass
``production_meshing_overrides()`` in ``PYFLUENT_RUN_OVERRIDES``:
``geometry_suffix`` ``.pmdb`` and ``geometry_root`` set to this output root.
Meshes and runs stay under ``RO_DATA_ROOT``.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.solver_common import sha256_file

DEFAULT_SOURCE_ROOT = "C:/ro_data/geometries"
DEFAULT_OUTPUT_ROOT = "C:/ro_data_mfbo/geometries_pmdb"
_META_NAME = "_meta.json"
_ERROR_NAME = "_convert_error.json"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def production_meshing_overrides() -> dict[str, str]:
    """Per-job overrides. Does not change run_config defaults."""
    return {
        "geometry_suffix": ".pmdb",
        "geometry_root": DEFAULT_OUTPUT_ROOT,
    }


def production_switch_text() -> str:
    overrides = production_meshing_overrides()
    return (
        "Production meshing still defaults to geometry_suffix '.dsco' and "
        "geometry_root '' (RO_DATA_ROOT/geometries). "
        "To mesh these .pmdb files, keep RO_DATA_ROOT on the campaign tree "
        "and pass PYFLUENT_RUN_OVERRIDES "
        f"{json.dumps(overrides)}. "
        "Do not copy the .pmdb files into C:/ro_data."
    )


def _folded(text: str) -> str:
    return text.replace("\\", "/").casefold().rstrip("/")


def _is_windows_absolute(text: str) -> bool:
    folded = text.replace("\\", "/")
    return len(folded) >= 3 and folded[1] == ":" and folded[2] == "/"


def _is_absolute(text: str) -> bool:
    return _is_windows_absolute(text) or Path(text).is_absolute()


def _under_campaign_data(path) -> bool:
    texts = [str(path), Path(path).as_posix()]
    candidate = Path(path)
    raw = str(path)
    if candidate.is_absolute() and not _is_windows_absolute(raw):
        try:
            texts.append(candidate.resolve().as_posix())
        except OSError:
            pass
    for text in texts:
        folded = _folded(text)
        if folded == "c:/ro_data" or folded.startswith("c:/ro_data/"):
            return True
        if folded == "/mnt/c/ro_data" or folded.startswith("/mnt/c/ro_data/"):
            return True
    return False


def resolve_source_root(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source root is required.")
    text = value.strip()
    if not _is_absolute(text):
        raise ValueError(f"source root must be absolute, got {value!r}.")
    return Path(text)


def resolve_output_root(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output root is required.")
    text = value.strip()
    if not _is_absolute(text):
        raise ValueError(f"output root must be absolute, got {value!r}.")
    if _under_campaign_data(text):
        raise ValueError(f"Refusing to write under C:/ro_data: {value}")
    return Path(text)


def select_geo_ids(only: str | None) -> list[str]:
    if only is None:
        return list(CAMPAIGN_GEO_ID_ORDER)
    if only not in CAMPAIGN_GEO_ID_ORDER:
        raise ValueError(
            f"Unknown campaign geo_id {only!r}. "
            f"Expected one of {', '.join(CAMPAIGN_GEO_ID_ORDER)}."
        )
    return [only]


def source_dsco(source_root: Path, geo_id: str) -> Path:
    family = family_for_geo_id(geo_id)
    return source_root / family / geo_id / f"{geo_id}.dsco"


def output_dir(output_root: Path, geo_id: str) -> Path:
    return output_root / family_for_geo_id(geo_id) / geo_id


def conversion_jobs() -> list[dict]:
    root = str(repo_root())
    return [
        {
            "id": f"convert-{geo_id}",
            "argv": [
                "{python}",
                "scripts/mfbo/convert_campaign_dsco_to_pmdb.py",
                "--only",
                geo_id,
            ],
            "cwd": root,
        }
        for geo_id in CAMPAIGN_GEO_ID_ORDER
    ]


def write_queue(path, jobs) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    return destination


def launch_discovery_modeler():
    """Hidden Discovery 25.1. Same keywords as ``pillar_cad``."""
    from ansys.geometry.core.connection.backend import ApiVersions
    from ansys.geometry.core.connection.launcher import (
        launch_modeler_with_discovery,
    )

    return launch_modeler_with_discovery(
        version=251,
        api_version=ApiVersions.V_251,
        hidden=True,
    )


def _exported_path(returned, out_dir: Path, design_name: str, suffix: str) -> Path:
    if returned:
        return Path(returned)
    path = out_dir / f"{design_name}{suffix}"
    if not path.is_file():
        raise RuntimeError(f"Export did not write {path}.")
    return path


def _place_export(returned, out_dir: Path, design_name: str, geo_id: str, suffix: str) -> Path:
    source = _exported_path(returned, out_dir, design_name, suffix)
    destination = out_dir / f"{geo_id}{suffix}"
    if source.resolve() != destination.resolve():
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite {destination}.")
        source.replace(destination)
    if not destination.is_file():
        raise RuntimeError(f"Export did not write {destination}.")
    return destination


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def convert_one(geo_id: str, *, source_root: Path, output_root: Path, launch) -> dict:
    """Convert one geometry. Exceptions become a failed record."""
    family = family_for_geo_id(geo_id)
    source = source_dsco(source_root, geo_id)
    out_dir = output_dir(output_root, geo_id)
    try:
        return _convert_session(
            geo_id=geo_id,
            family=family,
            source=source,
            out_dir=out_dir,
            launch=launch,
        )
    except Exception as exc:
        record = {
            "geo_id": geo_id,
            "family": family,
            "status": "failed",
            "source_dsco": str(source),
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(out_dir / f"{geo_id}{_ERROR_NAME}", record)
        print(traceback.format_exc(), file=sys.stderr)
        return record


def _convert_session(*, geo_id, family, source, out_dir, launch):
    pmdb_path = out_dir / f"{geo_id}.pmdb"
    scdocx_path = out_dir / f"{geo_id}.scdocx"
    meta_path = out_dir / f"{geo_id}{_META_NAME}"
    existing = [str(path) for path in (pmdb_path, scdocx_path, meta_path) if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing files: " + ", ".join(existing)
        )
    if not source.is_file():
        raise FileNotFoundError(f"Campaign .dsco not found: {source}")
    source_sha = sha256_file(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    modeler = None
    try:
        modeler = launch()
        design = modeler.open_file(str(source), upload_to_server=False)
        design_name = design.name
        pmdb = _place_export(
            design.export_to_pmdb(out_dir), out_dir, design_name, geo_id, ".pmdb"
        )
        scdocx = _place_export(
            design.export_to_scdocx(out_dir), out_dir, design_name, geo_id, ".scdocx"
        )
    finally:
        if modeler is not None:
            modeler.close()
    record = {
        "geo_id": geo_id,
        "family": family,
        "status": "converted",
        "source_dsco": str(source),
        "source_sha256": source_sha,
        "design_name": design_name,
        "pmdb": str(pmdb),
        "pmdb_sha256": sha256_file(pmdb),
        "scdocx": str(scdocx),
        "scdocx_sha256": sha256_file(scdocx),
    }
    _write_json(meta_path, record)
    error_path = out_dir / f"{geo_id}{_ERROR_NAME}"
    if error_path.exists():
        error_path.unlink()
    return record


def convert_all(geo_ids, *, source_root, output_root, launch) -> list[dict]:
    return [
        convert_one(
            geo_id,
            source_root=source_root,
            output_root=output_root,
            launch=launch,
        )
        for geo_id in geo_ids
    ]


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Convert campaign .dsco files to .pmdb. "
            "--emit-queue writes a job queue and does not launch Discovery."
        ),
    )
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--only", default=None, help="One campaign geo_id.")
    parser.add_argument(
        "--emit-queue",
        type=Path,
        help="Write the conversion job queue and do not convert.",
    )
    return parser


def main(argv=None, launch=None) -> int:
    args = build_parser().parse_args(argv)
    if args.emit_queue is not None:
        if args.only is not None:
            raise ValueError("--emit-queue does not take --only.")
        path = write_queue(args.emit_queue, conversion_jobs())
        print(path)
        print(production_switch_text())
        return 0
    source_root = resolve_source_root(args.source_root)
    output_root = resolve_output_root(args.output_root)
    if _folded(str(source_root)) == _folded(str(output_root)):
        raise ValueError("output root must not be the source root.")
    geo_ids = select_geo_ids(args.only)
    records = convert_all(
        geo_ids,
        source_root=source_root,
        output_root=output_root,
        launch=launch_discovery_modeler if launch is None else launch,
    )
    if args.only is None:
        _write_json(output_root / "conversion_summary.json", {"records": records})
    failed = [record["geo_id"] for record in records if record["status"] != "converted"]
    if failed:
        print("Failed: " + ", ".join(failed), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"CONVERT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
