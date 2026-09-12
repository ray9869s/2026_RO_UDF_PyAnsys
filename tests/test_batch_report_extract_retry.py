"""batch_report_extract retries Fluent session deaths on a fresh subprocess."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import load_batch_report_extract
from ro.session_retry import RETRY_KIND_SCHEME_HEAP, RETRY_KIND_SOCKET_RESET

SOCKET_RESET_ERROR = (
    "RuntimeError: IOCP/Socket: Connection reset "
    "(An existing connection was forcibly closed by the remote host, 10054)"
)
SCHEME_HEAP_ERROR = (
    "RuntimeError: wta(1st) to string->symbol\n"
    "Error Object: #[free (3 cells)]\n"
    "Error: Attempt to mark a free block\n"
    "Error encountered in critical code section"
)
LOAD_BEARING_ERROR = (
    "Load-bearing report compute failed: pp_m_in: missing or None"
)


@pytest.fixture
def batch_extract():
    return load_batch_report_extract()


def test_format_selected_post_case_prints_four_id(batch_extract):
    line = batch_extract.format_selected_post_case(
        1,
        2,
        {
            "family": "diamond",
            "geo_id": "D0817_a60",
            "mesh_id": "max060_min006_cpg5_bl4_peel2",
            "run_id": "u0p2_p6M",
        },
    )
    assert line == (
        "  1/2  diamond/D0817_a60/max060_min006_cpg5_bl4_peel2/u0p2_p6M"
    )


def test_retries_scheme_heap_then_succeeds(batch_extract, tmp_path: Path):
    calls = []
    sleeps = []

    def runner(cmd, env, log_path):
        calls.append(Path(log_path).name)
        text = SCHEME_HEAP_ERROR if len(calls) == 1 else "ok"
        Path(log_path).write_text(text, encoding="utf-8")
        return_code = 1 if len(calls) == 1 else 0
        return type("R", (), {"returncode": return_code})()

    result, attempts, kinds, logs = batch_extract.run_extract_attempts(
        cmd=["python", "scripts/pyfluent_report_extract.py"],
        env={},
        run_directory=tmp_path,
        geo_id="D0817_a30",
        mesh_id="max085_min006_cpg5_bl4_peel2",
        run_id="u0p2_p6M",
        max_retries=2,
        settle_s=15.0,
        sleeper=sleeps.append,
        runner=runner,
        process_lister=lambda _name: ([], None),
    )
    assert result.returncode == 0
    assert attempts == 2
    assert kinds == [RETRY_KIND_SCHEME_HEAP]
    assert sleeps == [15.0]
    assert calls[0].endswith("__extract_attempt1.log")
    assert "max085" in calls[0]
    assert calls[1].endswith("__extract_attempt2.log")
    assert [path.name for path in logs] == calls


def test_retries_socket_reset(batch_extract, tmp_path: Path):
    calls = []

    def runner(cmd, env, log_path):
        calls.append(1)
        Path(log_path).write_text(SOCKET_RESET_ERROR, encoding="utf-8")
        return type("R", (), {"returncode": 1})()

    result, attempts, kinds, _logs = batch_extract.run_extract_attempts(
        cmd=["python", "scripts/pyfluent_report_extract.py"],
        env={},
        run_directory=tmp_path,
        geo_id="D0817_a60",
        mesh_id="max060_min006_cpg5_bl4_peel2",
        run_id="u0p2_p6M",
        max_retries=2,
        settle_s=0.0,
        sleeper=lambda _s: None,
        runner=runner,
        process_lister=lambda _name: ([], None),
    )
    assert result.returncode == 1
    assert attempts == 3
    assert kinds == [RETRY_KIND_SOCKET_RESET, RETRY_KIND_SOCKET_RESET]
    assert calls == [1, 1, 1]


def test_does_not_retry_load_bearing(batch_extract, tmp_path: Path):
    calls = []

    def runner(cmd, env, log_path):
        calls.append(1)
        Path(log_path).write_text(LOAD_BEARING_ERROR, encoding="utf-8")
        return type("R", (), {"returncode": 1})()

    result, attempts, kinds, _logs = batch_extract.run_extract_attempts(
        cmd=["python", "scripts/pyfluent_report_extract.py"],
        env={},
        run_directory=tmp_path,
        geo_id="D0817_a30",
        mesh_id="max085_min006_cpg5_bl4_peel2",
        run_id="u0p2_p6M",
        max_retries=2,
        settle_s=15.0,
        sleeper=lambda _s: None,
        runner=runner,
        process_lister=lambda _name: ([], None),
    )
    assert result.returncode == 1
    assert attempts == 1
    assert kinds == []
    assert calls == [1]
