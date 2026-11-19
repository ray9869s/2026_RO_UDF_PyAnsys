"""Setup-expression validity read from Fluent's own transcript output.

Fluent 25.1 has none of the scheme check forms tried earlier
(``get-invalid-inputs-formated``, ``get-invalid-inputs``, ``expr-check``,
``validate-named-expr-def``, ``validate-expr-report-def`` all return
``eval: unbound variable``; host smoke at 3ba17ec). What Fluent does print,
after a settings change while any expression is invalid, is::

    Setup contains invalid expression.
        Expression   Error   UsedIn
        ...          name 'x' is not defined
            <object> (Report Definition)
    Setup contains invalid expressions.

Each such table lists every expression invalid at that moment; nothing is
printed while all are valid. The template case prints tables for ``lmh``
(``rho_avg`` not defined) between ``replace_mesh`` and the transport-report
rebuild, which removes the stale ``rho_avg`` and redefines ``lmh`` with a
constant density. Those tables are expected and must not refuse.

So the solver writes a BEGIN sentinel (with a per-attempt token) through the
scheme interface right after that rebuild, and an END sentinel at the check
before ``iterate``. Fluent echoes every scheme command into the transcript,
so both land in this attempt's ``.trn``. A table between the two sentinels
refuses. No END sentinel within the wait means the check could not be read;
that is the only case ``RO_EXPRESSION_CHECK=warn`` may override.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

BEGIN = "RO_EXPRESSION_WINDOW_BEGIN"
END = "RO_EXPRESSION_WINDOW_END"
INVALID_HEADER = "setup contains invalid expression"
WINDOW_WAIT_S = 60.0
WINDOW_POLL_S = 1.0


def new_token() -> str:
    return uuid.uuid4().hex[:16]


def marker(kind: str, token: str) -> str:
    return f"{kind} token={token}"


def scheme_marker_command(kind: str, token: str) -> str:
    """Scheme that echoes (and displays) one sentinel line."""
    return f'(begin (newline) (display "{marker(kind, token)}") (newline))'


def extract_window(text, token):
    """Text between this token's BEGIN and END sentinels, or None."""
    if not isinstance(text, str) or not token:
        return None
    begin_text = marker(BEGIN, token)
    end_text = marker(END, token)
    start = text.find(begin_text)
    if start < 0:
        return None
    start += len(begin_text)
    stop = text.find(end_text, start)
    if stop < 0:
        return None
    return text[start:stop]


def invalid_tables(window) -> list[str]:
    """Every ``Setup contains invalid expression`` table in ``window``."""
    tables: list[str] = []
    current: list[str] | None = None
    for line in (window or "").splitlines():
        lowered = line.strip().casefold()
        if lowered.startswith(INVALID_HEADER):
            closing = lowered.startswith(INVALID_HEADER + "s")
            if current is not None and closing:
                current.append(line)
                tables.append("\n".join(current))
                current = None
                continue
            if current is not None:
                tables.append("\n".join(current))
            current = [line]
            continue
        if current is not None:
            current.append(line)
            if len(current) > 60:  # unterminated: keep it, stop growing
                tables.append("\n".join(current))
                current = None
    if current is not None:
        tables.append("\n".join(current))
    return tables


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def find_window(paths, token, *, reader=_read):
    """``(path, window)`` from the first file holding both sentinels."""
    for path in paths:
        text = reader(path)
        window = extract_window(text, token)
        if window is not None:
            return path, window
    return None, None


def wait_for_window(paths_fn, token, *, timeout_s=WINDOW_WAIT_S, poll_s=WINDOW_POLL_S,
                    reader=_read, sleep=time.sleep, clock=time.monotonic):
    """Poll the transcripts until this token's window is complete."""
    deadline = clock() + float(timeout_s)
    while True:
        path, window = find_window(paths_fn(), token, reader=reader)
        if window is not None or clock() >= deadline:
            return path, window
        sleep(min(float(poll_s), max(0.0, deadline - clock())))


def window_verdict(window) -> tuple[str, list[str]]:
    """``("unverified", [])``, ``("invalid", tables)`` or ``("pass", [])``."""
    if window is None:
        return "unverified", []
    tables = invalid_tables(window)
    if tables or INVALID_HEADER in window.casefold():
        return "invalid", tables or [window.strip()]
    return "pass", []
