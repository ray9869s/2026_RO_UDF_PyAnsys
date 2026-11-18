#!/usr/bin/env bash
# Git Bash on the Windows host. Never pulls or starts a long solve queue.
set -euo pipefail
cd /c/pyfluent
if [[ ! -x .venv/Scripts/python.exe ]]; then
    echo 'FAIL: missing C:/pyfluent/.venv/Scripts/python.exe' >&2
    exit 1
fi
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
export RO_FLUENT_MPI=intel
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION PYFLUENT_RUN_OVERRIDES
unset PYFLUENT_POST_CONFIG PYFLUENT_POST_OVERRIDES
exec .venv/Scripts/python.exe -u scripts/host/pending_checks.py "$@"
