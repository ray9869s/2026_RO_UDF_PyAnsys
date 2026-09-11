"""Fluent session-death classification: socket, Scheme heap, launch/spawn."""

from __future__ import annotations

from ro.session_retry import (
    RETRY_KIND_LAUNCH_SPAWN,
    RETRY_KIND_SCHEME_HEAP,
    RETRY_KIND_SOCKET_RESET,
    classify_retryable_session_crash,
    describe_attempt_orphans,
    parse_sifile_names,
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


def test_orphan_scan_failure_is_not_no_process():
    def lister(name):
        raise OSError("wmic unavailable")

    lines = describe_attempt_orphans(LAUNCH_SPAWN, process_lister=lister)
    joined = "\n".join(lines)
    assert "could not be read" in joined
    assert "Not treating that as 'no process'" in joined
