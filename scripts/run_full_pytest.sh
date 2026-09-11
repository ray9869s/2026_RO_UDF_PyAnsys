#!/usr/bin/env bash
# Local full-suite runner. Requires the existing project .venv.
# CI must not call this script: a fresh runner has no .venv.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "scripts/run_full_pytest.sh: missing executable $PYTHON" >&2
    exit 1
fi

export PYTHONDONTWRITEBYTECODE=1
exec "$PYTHON" -m pytest -q -p no:cacheprovider "$@"
