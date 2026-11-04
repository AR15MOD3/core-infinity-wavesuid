#!/usr/bin/env bash
# Explicit scoped install. Use --plan for a no-write, no-download preflight.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE=""; OWNER=""; CONFIG=""; DATA=""; PORT=""
DOWNLOADS=0; PLAYWRIGHT=0; PLAN=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --service-dir) SERVICE="${2:?path required}"; shift ;;
    --owner) OWNER="${2:?Bot ID required}"; shift ;;
    --config-path) CONFIG="${2:?path required}"; shift ;;
    --data-dir) DATA="${2:?path required}"; shift ;;
    --port) PORT="${2:?port required}"; shift ;;
    --allow-downloads) DOWNLOADS=1 ;;
    --playwright) PLAYWRIGHT=1 ;;
    --plan) PLAN=1 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
: "${SERVICE:?--service-dir required}" "${OWNER:?--owner required}" "${CONFIG:?--config-path required}" "${DATA:?--data-dir required}" "${PORT:?--port required}"
command -v python3 >/dev/null || { echo 'Install Python explicitly first.' >&2; exit 2; }
PY="$SERVICE/.venv/bin/python"
ARGS=("$ROOT/tools/provision.py" --service-dir "$SERVICE" --owner "$OWNER" --config-path "$CONFIG" --data-dir "$DATA" --port "$PORT" --python "$PY")
python3 "${ARGS[@]}"
[ "$PLAN" = 0 ] || exit 0
[ "$DOWNLOADS" = 1 ] || { echo 'Explicit --allow-downloads required; no writes performed.' >&2; exit 2; }
command -v uv >/dev/null && command -v git >/dev/null || { echo 'Install uv and git explicitly first.' >&2; exit 2; }
python3 "${ARGS[@]}" --apply
[ -x "$PY" ] || uv venv --python "${PYTHON_VERSION:-3.11}" "$SERVICE/.venv"
uv pip install --python "$PY" -r "$SERVICE/pyproject.toml"
"$PY" "$SERVICE/tools/update_plugins.py"
if [ "$PLAYWRIGHT" = 1 ]; then
  uv pip install --python "$PY" playwright
  "$PY" -m playwright install chromium
fi
python3 "${ARGS[@]}" --apply --write-config
printf '%s\n' 'Service staged. Existing scoped config is preserved; review serviceDir explicitly before enable.' 'No Bot started, no QQ sent, no account data copied. PNG requires --playwright.'
