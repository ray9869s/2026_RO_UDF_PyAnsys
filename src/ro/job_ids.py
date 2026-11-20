"""Queue job ids: the one pattern job_queue accepts, shared by every emitter.

An id names the job's log and state files, so it is filename-safe and at
most 80 characters. Emitters build short ids (``rx-<geo>``,
``snap-<geo>-<N>``) and check them here before writing a queue, so a
queue is never written with an id the runner will refuse.
"""

from __future__ import annotations

import re

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def job_id_valid(value) -> bool:
    return isinstance(value, str) and JOB_ID_RE.fullmatch(value) is not None


def require_job_id(value) -> str:
    if not job_id_valid(value):
        raise ValueError(
            f"job id must be filename-safe and at most 80 characters "
            f"(match {JOB_ID_RE.pattern}), got {value!r}."
        )
    return value


def require_job_ids(jobs) -> None:
    """Every queue writer calls this before writing: valid and unique ids."""
    seen = set()
    for job in jobs:
        job_id = require_job_id(job.get("id") if isinstance(job, dict) else None)
        if job_id in seen:
            raise ValueError(f"duplicate job id {job_id!r}.")
        seen.add(job_id)
