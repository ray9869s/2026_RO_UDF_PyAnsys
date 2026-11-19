"""Fluent session-death classification: socket, Scheme heap, launch/spawn."""

from __future__ import annotations

from types import SimpleNamespace

from ro.session_retry import (
    RETRY_KIND_LAUNCH_SPAWN,
    RETRY_KIND_SCHEME_HEAP,
    RETRY_KIND_SOCKET_RESET,
    classify_retryable_session_crash,
    describe_attempt_orphans,
    format_fluent_launch_line,
    parse_fluent_launch_pids,
    parse_sifile_names,
    terminate_recorded_fluent_pids,
)

SOCKET_RESET = (
    "RuntimeError: IOCP/Socket: Connection reset "
    "(An existing connection was forcibly closed by the remote host, 10054)"
)
SCHEME_HEAP = (
    "RuntimeError: wta(1st) to string->symbol\n"
    "Error Object: #[free (3 cells)]\n"
    "Error: Attempt to mark a free block\n"
    "Error encountered in critical code section"
)
LAUNCH_SPAWN = (
    "ansys.fluent.core.launcher.error_handler.LaunchFluentError:\n"
    "Fluent Launch command: fluent 3ddp -sifile="
    r"C:\Users\me\AppData\Local\Temp\serverinfo-abc123.txt"
    "\ngrpc._channel._InactiveRpcError: Deadline Exceeded in health_check\n"
    "Failed to construct hwtree for collect command. 0x8000ffff\n"
    "Aborting:"
)
INLET_READBACK = "RuntimeError: Inlet BC readback failed on inlet: x-velocity UDF '' != requested RO_inlet_profile"
G_MARKER = "RuntimeError: Missing required transcript marker RO_UDF_INLET_G"
UDF_COMPILE = "RuntimeError: UDF compilation failed for libudf"
QOI_FAIL = "stop_reason=qoi_not_converged after max QoI window"
RESIDUAL_FAIL = "stop_reason=residual_diverged"
MANIFEST_FAIL = "Run manifest missing required field mesh_sha256"


def test_socket_scheme_and_launch_are_retryable():
    assert classify_retryable_session_crash(SOCKET_RESET) == RETRY_KIND_SOCKET_RESET
    assert classify_retryable_session_crash(SCHEME_HEAP) == RETRY_KIND_SCHEME_HEAP
    assert classify_retryable_session_crash(LAUNCH_SPAWN) == RETRY_KIND_LAUNCH_SPAWN
    assert classify_retryable_session_crash("LaunchFluentError") == RETRY_KIND_LAUNCH_SPAWN
    assert classify_retryable_session_crash("Deadline Exceeded") == RETRY_KIND_LAUNCH_SPAWN
    assert classify_retryable_session_crash("Failed to construct hwtree") == (
        RETRY_KIND_LAUNCH_SPAWN
    )
    assert classify_retryable_session_crash("Aborting:") == RETRY_KIND_LAUNCH_SPAWN


def test_session_signature_anywhere_wins_over_non_retryable_wrapper():
    wrapped_scheme = (
        "Canonical CP cannot be computed: segmented membrane CP failed "
        f"({SCHEME_HEAP})"
    )
    wrapped_launch = f"{INLET_READBACK}\n{LAUNCH_SPAWN}"
    wrapped_socket = f"{G_MARKER}\n{SOCKET_RESET}"
    assert classify_retryable_session_crash(wrapped_scheme) == RETRY_KIND_SCHEME_HEAP
    assert classify_retryable_session_crash(wrapped_launch) == RETRY_KIND_LAUNCH_SPAWN
    assert classify_retryable_session_crash(wrapped_socket) == RETRY_KIND_SOCKET_RESET


def test_deterministic_failures_are_not_retryable():
    for text in (
        INLET_READBACK,
        G_MARKER,
        UDF_COMPILE,
        QOI_FAIL,
        RESIDUAL_FAIL,
        MANIFEST_FAIL,
        "",
        None,
    ):
        assert classify_retryable_session_crash(text or "") is None


def test_parse_sifile_from_launch_command():
    names = parse_sifile_names(LAUNCH_SPAWN)
    assert names == ["serverinfo-abc123.txt"]


def test_orphan_log_without_sifile_is_manual():
    lines = describe_attempt_orphans(INLET_READBACK)
    assert any("cannot be targeted" in line for line in lines)
    assert not any("ORPHAN-PID" in line for line in lines)


def test_orphan_log_lists_only_sifile_matches():
    def lister(name):
        assert name == "serverinfo-abc123.txt"
        return ["1234 fluent.exe -sifile=serverinfo-abc123.txt"]

    lines = describe_attempt_orphans(LAUNCH_SPAWN, process_lister=lister)
    joined = "\n".join(lines)
    assert "ORPHAN-PID: 1234 fluent.exe" in joined
    assert "do not kill other fluent.exe" in joined


def test_launch_line_records_the_launcher_and_fluent_own_pids():
    class _Process:
        pid = 4242

    class _Session:
        _process = _Process()
        _fluent_connection = SimpleNamespace(
            connection_properties=SimpleNamespace(cortex_pid=5151, fluent_host_pid=6161)
        )

    line = format_fluent_launch_line(_Session())
    assert line == "FLUENT_LAUNCH pid=4242 cortex_pid=5151 fluent_host_pid=6161"
    assert format_fluent_launch_line(object()) == (
        "FLUENT_LAUNCH pid=unknown cortex_pid=unknown fluent_host_pid=unknown"
    )
    text = (
        line + "\n"
        "FLUENT_LAUNCH pid=unknown cortex_pid=5151 fluent_host_pid=7171\n"
        "fluent-20261019-114250-19328.trn\n"
        "-sifile=serverinfo-other.txt\n"
        "ORPHAN noise pid=99999\n"
    )
    assert parse_fluent_launch_pids(text) == [4242, 5151, 6161, 7171]
    assert parse_fluent_launch_pids("FLUENT_LAUNCH pid=4242\n") == [4242]  # old logs


def test_recorded_fluent_tree_is_killed_and_verified(monkeypatch):
    monkeypatch.setattr("ro.session_retry.os.name", "nt")
    table = [
        {"ProcessId": 31240, "ParentProcessId": 1, "Name": "python.exe"},  # reused pid
        {"ProcessId": 5151, "ParentProcessId": 1, "Name": "fluent.exe"},
        {"ProcessId": 6161, "ParentProcessId": 5151, "Name": "fl2510.exe"},
        {"ProcessId": 7000, "ParentProcessId": 6161, "Name": "fl_mpi2510.exe"},
        {"ProcessId": 19328, "ParentProcessId": 1, "Name": "fluent.exe"},  # another session
    ]
    killed = []

    def runner(argv, **kwargs):
        killed.append(int(argv[2]))
        return SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")

    def lister():
        return [row for row in table if row["ProcessId"] not in killed]

    lines = terminate_recorded_fluent_pids(
        "FLUENT_LAUNCH pid=31240 cortex_pid=5151 fluent_host_pid=6161\n"
        "fluent-20261019-114250-19328.trn\n",
        runner=runner, lister=lister, verify_kill_s=0,
    )
    assert sorted(killed) == [5151, 6161, 7000]
    joined = "\n".join(lines)
    assert "31240 is now 'python.exe'" in joined
    assert "KILL_VERIFIED pids=5151,6161,7000" in joined
    assert "19328" not in joined


def test_recorded_fluent_tree_survivor_is_incomplete(monkeypatch):
    monkeypatch.setattr("ro.session_retry.os.name", "nt")
    table = [{"ProcessId": 6161, "ParentProcessId": 1, "Name": "fl2510.exe"}]
    lines = terminate_recorded_fluent_pids(
        "FLUENT_LAUNCH pid=unknown cortex_pid=unknown fluent_host_pid=6161\n",
        runner=lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="Access denied"),
        lister=lambda: table, verify_kill_s=0,
    )
    assert "KILL_INCOMPLETE survivors=6161" in "\n".join(lines)

    def broken():
        raise OSError("CIM down")

    lines = terminate_recorded_fluent_pids("FLUENT_LAUNCH pid=6161\n", lister=broken)
    assert "KILL_INCOMPLETE" in lines[0] and "CIM down" in lines[0]


def test_orphan_scan_failure_is_not_no_process():
    def lister(name):
        raise OSError("wmic unavailable")

    lines = describe_attempt_orphans(LAUNCH_SPAWN, process_lister=lister)
    joined = "\n".join(lines)
    assert "could not be read" in joined
    assert "Not treating that as 'no process'" in joined


def test_worker_cleanup_waits_for_a_clean_exit_then_kills_what_is_left(monkeypatch, capsys):
    import ro.session_retry as retry

    monkeypatch.setattr(retry.os, "name", "nt")
    monkeypatch.setattr(retry, "_LAUNCHED_PIDS", [])

    class _Session:
        _process = SimpleNamespace(pid=31240)
        _fluent_connection = SimpleNamespace(
            connection_properties=SimpleNamespace(cortex_pid=5151, fluent_host_pid=6161)
        )

    retry.print_fluent_launch_identity(_Session())
    retry.print_fluent_launch_identity(_Session())  # after switch_to_solver: same pids
    assert retry._LAUNCHED_PIDS == [31240, 5151, 6161]
    gone = retry.stop_launched_fluent(grace_s=0.01, lister=lambda: [])
    assert "not running" in gone[0]
    table = [{"ProcessId": 6161, "ParentProcessId": 1, "Name": "fl2510.exe"}]
    killed = []

    def runner(argv, **kwargs):
        killed.append(argv[2])
        table.clear()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    lines = retry.stop_launched_fluent(grace_s=0.01, lister=lambda: list(table), runner=runner)
    assert killed == ["6161"]
    assert "KILL_VERIFIED pids=6161" in lines[-1]
    assert "FLUENT_EXIT" in capsys.readouterr().out


def test_orphans_of_an_exited_launcher_are_found_and_unrelated_fluent_is_kept(monkeypatch):
    """3ba17ec: 31240 had exited; fluent.exe / fl2510.exe still pointed at it."""
    monkeypatch.setattr("ro.session_retry.os.name", "nt")
    table = [
        {"ProcessId": 900, "ParentProcessId": 31240, "Name": "fluent.exe"},
        {"ProcessId": 901, "ParentProcessId": 900, "Name": "fl2510.exe"},
        {"ProcessId": 902, "ParentProcessId": 901, "Name": "mpiexec.exe"},
        {"ProcessId": 950, "ParentProcessId": 31240, "Name": "notepad.exe"},  # not adopted
        {"ProcessId": 19328, "ParentProcessId": 4, "Name": "fluent.exe"},  # other session
    ]
    killed = []

    def runner(argv, **kwargs):
        killed.append(int(argv[2]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    lines = terminate_recorded_fluent_pids(
        "FLUENT_LAUNCH pid=31240 cortex_pid=unknown fluent_host_pid=unknown\n",
        runner=runner,
        lister=lambda: [row for row in table if row["ProcessId"] not in killed],
        verify_kill_s=0,
    )
    assert sorted(killed) == [900, 901, 902]
    assert "KILL_VERIFIED pids=900,901,902" in "\n".join(lines)


def test_fluent_launcher_above_cortex_is_part_of_the_session(monkeypatch):
    monkeypatch.setattr("ro.session_retry.os.name", "nt")
    table = [
        {"ProcessId": 10, "ParentProcessId": 1, "Name": "python.exe"},
        {"ProcessId": 800, "ParentProcessId": 10, "Name": "fluent.exe"},
        {"ProcessId": 5151, "ParentProcessId": 800, "Name": "cx2510.exe"},
        {"ProcessId": 6161, "ParentProcessId": 5151, "Name": "fl2510.exe"},
    ]
    killed = []
    lines = terminate_recorded_fluent_pids(
        "FLUENT_LAUNCH pid=31240 cortex_pid=5151 fluent_host_pid=6161\n",
        runner=lambda argv, **kw: killed.append(int(argv[2])) or SimpleNamespace(returncode=0, stdout="", stderr=""),
        lister=lambda: [row for row in table if row["ProcessId"] not in killed],
        verify_kill_s=0,
    )
    assert sorted(killed) == [800, 5151, 6161]  # python parent untouched
    assert "KILL_VERIFIED" in "\n".join(lines)
