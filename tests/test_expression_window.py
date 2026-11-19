"""Expression validity from Fluent's transcript (host smoke at 3ba17ec)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_solver_code
from ro.expression_window import (
    BEGIN,
    END,
    extract_window,
    invalid_tables,
    marker,
    scheme_marker_command,
    wait_for_window,
    window_verdict,
)

# Lines copied from the 3ba17ec transcript (tabs as Fluent wrote them).
LMH_TABLE = (
    "Setup contains invalid expression.\n"
    "\n"
    "                                       Expression\t\t                         Error\t\t                 UsedIn\n"
    "                                       ==========\t\t                         =====\t\t                 ======\n"
    "( m_in + m_out ) / ( rho_avg * area_mem ) * 3.6e6\t\tname 'rho_avg' is not defined\n"
    "\t\tlmh (Report Definition)\n"
    "Setup contains invalid expressions.\n"
)
CP_LIVE_TABLE = (
    "Setup contains invalid expression.\n"
    "\n"
    "                                         Expression\t\t                              Error\t\t                     UsedIn\n"
    "                                         ==========\t\t                              =====\t\t                     ======\n"
    "cp_live_mean / (cp_monitor_cb_yi * 998.2 / 0.05844)\t\tname 'cp_live_mean' is not defined\n"
    "\t\tcp_live (Report Definition)\n"
    "Setup contains invalid expressions.\n"
)
SETUP_TAIL = (
    "Hybrid initialization is done.\n"
    "RO_UDF_INLET_PROFILE_G=1.00075245346\n"
    "pp_membrane_z_facetmax_m\n"
    "        pp_membrane_z_facetmax_m       0.00038499999\n"
)
TOKEN = "0123456789abcdef"


def _echo(kind):
    return scheme_marker_command(kind, TOKEN) + "\n" + marker(kind, TOKEN) + "\n"


def _transcript(*, cp_live=False, end=True):
    text = " (update-solver-thread-names)\n" + LMH_TABLE * 7
    text += "/define/user-defined/user-defined-memory 13\n" + _echo(BEGIN) + SETUP_TAIL
    if cp_live:
        text += CP_LIVE_TABLE
    text += "Writing to ...setup.cas.h5\nDone.\n"
    if end:
        text += _echo(END)
    return text


def test_template_lmh_tables_before_begin_do_not_count():
    window = extract_window(_transcript(), TOKEN)
    assert window is not None and "rho_avg" not in window
    assert window_verdict(window) == ("pass", [])


def test_cp_live_table_inside_the_window_refuses_and_is_quoted():
    verdict, tables = window_verdict(extract_window(_transcript(cp_live=True), TOKEN))
    assert verdict == "invalid"
    assert len(tables) == 1 and "name 'cp_live_mean' is not defined" in tables[0]
    assert tables[0].rstrip().endswith("Setup contains invalid expressions.")


def test_missing_end_or_other_token_is_unverified():
    assert extract_window(_transcript(end=False), TOKEN) is None
    assert extract_window(_transcript(), "ffffffffffffffff") is None
    assert window_verdict(None) == ("unverified", [])


def test_consecutive_tables_are_split():
    assert len(invalid_tables(LMH_TABLE + CP_LIVE_TABLE)) == 2


def test_wait_polls_until_the_end_sentinel_lands(tmp_path):
    trn = tmp_path / "fluent-1.trn"
    trn.write_text(_transcript(end=False), encoding="utf-8")
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds
        trn.write_text(_transcript(), encoding="utf-8")

    path, window = wait_for_window(lambda: [trn], TOKEN, timeout_s=5, poll_s=1,
                                   sleep=sleep, clock=lambda: clock["t"])
    assert path == trn and window is not None


class _Scheme:
    """Writes each scheme command (and a sentinel's display) to the transcript."""

    def __init__(self, trn, before_end=""):
        self.trn = trn
        self.before_end = before_end

    def exec(self, commands, wait=True, silent=False):
        command = commands[0]
        with self.trn.open("a", encoding="utf-8") as handle:
            if END in command:
                handle.write(self.before_end)
            handle.write(command + "\n")
            for kind in (BEGIN, END):
                if kind in command:
                    token = command.split("token=")[1].split('"')[0]
                    handle.write(marker(kind, token) + "\n")
        return ""


def _session(scheme, names=()):
    class Named:
        def get_object_names(self):
            return list(names)

    return SimpleNamespace(scheme=scheme, setup=SimpleNamespace(named_expressions=Named()),
                           solution=SimpleNamespace())


def _run(tmp_path, before_end, monkeypatch=None, *, begin=True):
    solver = load_solver_code(f"expr_window_{abs(hash((before_end, begin)))}")
    trn = tmp_path / "fluent-20261119-1.trn"
    trn.write_text(LMH_TABLE * 3, encoding="utf-8")
    log = tmp_path / "solver_log_u0p2_p6M.txt"
    log.write_text("earlier\n", encoding="utf-8")
    session = _session(_Scheme(trn, before_end))
    token = solver.begin_expression_window(session) if begin else None
    trn.open("a", encoding="utf-8").write(SETUP_TAIL)
    solver.require_setup_expressions_valid(
        session, solver_log_path=log, case_dir=tmp_path, window_token=token,
        wait_s=0, sleep=lambda s: None,
    )
    return log.read_text(encoding="utf-8")


def test_solver_passes_with_only_template_tables(tmp_path):
    text = _run(tmp_path, "")
    assert "RESULT: PASS" in text and "rho_avg" not in text.split("transcript window")[1]


def test_solver_refuses_a_table_after_setup_even_in_warn_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("RO_EXPRESSION_CHECK", "warn")
    with pytest.raises(RuntimeError, match="Refusing to iterate") as caught:
        _run(tmp_path, CP_LIVE_TABLE)
    assert "cp_live_mean" in str(caught.value)
    log = (tmp_path / "solver_log_u0p2_p6M.txt").read_text(encoding="utf-8")
    assert "FAIL (invalid expression)" in log


def test_solver_unverified_refuses_unless_warn(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="unverified"):
        _run(tmp_path, "", begin=False)
    monkeypatch.setenv("RO_EXPRESSION_CHECK", "warn")
    text = _run(tmp_path, "", begin=False)
    assert "UNVERIFIED OVERRIDDEN" in text


def test_named_expression_listed_invalid_refuses(tmp_path):
    solver = load_solver_code("expr_window_named")
    trn = tmp_path / "fluent-1.trn"
    trn.write_text("", encoding="utf-8")

    class Scheme(_Scheme):
        def exec(self, commands, wait=True, silent=False):
            if "list-properties" in commands[0]:
                return "cp_live_mean\nType             : Unknown\nValid            : No\n"
            return super().exec(commands, wait, silent)

    session = _session(Scheme(trn), names=["cp_live_mean"])
    token = solver.begin_expression_window(session)
    with pytest.raises(RuntimeError, match="invalid expressions"):
        solver.require_setup_expressions_valid(
            session, solver_log_path=tmp_path / "solver_log.txt", case_dir=tmp_path,
            window_token=token, wait_s=0, sleep=lambda s: None,
        )


def test_the_nonexistent_scheme_forms_are_gone():
    source = Path(__file__).resolve().parents[1] / "scripts" / "solver_code_260616.py"
    text = source.read_text(encoding="utf-8")
    for form in ("get-invalid-inputs", "expr-check", "validate-named-expr-def",
                 "validate-expr-report-def"):
        assert form not in text
