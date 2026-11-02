"""Fluent process arguments shared by every launch on this host.

Microsoft MPI is Fluent's default on Windows once the machine has 64 or
more logical CPUs. That default aborts with
``Failed to construct hwtree for collect command. 0x8000ffff``
(Ansys KB2797241). ``-mpi=intel`` starts. An empty MPI name omits the
flag and leaves Fluent's default.
"""

from __future__ import annotations

import os
import re

FLUENT_MPI_ENV = "RO_FLUENT_MPI"
DEFAULT_FLUENT_MPI = "intel"
_TOKEN = re.compile(r"[A-Za-z0-9_-]*")


def fluent_mpi_token(value) -> str:
    """Strip and check one MPI name. Does not read the environment."""
    if not isinstance(value, str):
        raise ValueError(f"fluent_mpi must be a string, got {value!r}.")
    token = value.strip()
    if _TOKEN.fullmatch(token) is None:
        raise ValueError(
            "fluent_mpi must be empty or one token of letters, digits, "
            f"underscore, or hyphen, got {value!r}."
        )
    return token


def resolve_fluent_mpi(configured=None) -> str:
    """MPI name to launch with. ``RO_FLUENT_MPI`` wins, including when empty.

    ``configured`` is the ``run_config.py`` value. ``None`` uses
    ``DEFAULT_FLUENT_MPI`` when the environment variable is unset.
    """
    if FLUENT_MPI_ENV in os.environ:
        value = os.environ[FLUENT_MPI_ENV]
    elif configured is None:
        value = DEFAULT_FLUENT_MPI
    else:
        value = configured
    return fluent_mpi_token(value)


def fluent_mpi_argument(configured=None) -> str:
    """``-mpi=<name>``, or ``""`` when Fluent should choose its default."""
    value = resolve_fluent_mpi(configured)
    if not value:
        return ""
    return f"-mpi={value}"
