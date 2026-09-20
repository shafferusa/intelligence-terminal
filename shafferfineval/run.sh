#!/usr/bin/env bash
# ShafferFinEval launcher -- macOS and Linux.
#
# First run creates a virtual environment and installs the two dependencies.
# Every run after that just starts the terminal and opens a browser window.
#
#   ./run.sh              start the app
#   ./run.sh --refresh    run today's score refresh first, then start
#
# On macOS, double-clicking run.command does the same thing.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"
STAMP="$VENV/.deps-installed"
PORT="${SHAFFERFINEVAL_PORT:-8501}"
URL="http://127.0.0.1:${PORT}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: '$PYTHON' not found. Install Python 3.10 or newer, then re-run." >&2
  exit 1
fi

# 3.10+ is required: the engines use `list[dict]` and `X | None` annotations,
# which older versions reject at import time.
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || {
  echo "ERROR: Python 3.10+ required, found $("$PYTHON" -V 2>&1)." >&2
  exit 1
}

if [ ! -d "$VENV" ]; then
  echo "First run: creating $VENV and installing dependencies..."
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
fi

# Install on first run, and again whenever requirements.txt changes. The
# stamp is only written after a SUCCESSFUL install, so a failed one retries
# next time instead of leaving a half-built environment that looks ready.
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  if ! "$VENV/bin/python" -m pip install -r requirements.txt; then
    echo >&2
    echo "ERROR: could not install streamlit and requests." >&2
    echo "  The two usual causes are no internet connection, or a corporate" >&2
    echo "  proxy blocking PyPI. Check that this works:" >&2
    echo "    $VENV/bin/python -m pip install streamlit" >&2
    echo "  Nothing else about the app needs the network to start." >&2
    exit 1
  fi
  touch "$STAMP"
  echo "Dependencies ready."
fi

if [ "${1:-}" = "--refresh" ]; then
  echo "Refreshing scores before launch (this hits Yahoo and FRED)..."
  "$VENV/bin/python" daily_job.py || \
    echo "Refresh failed; starting anyway with whatever is already stored."
fi

echo "ShafferFinEval starting at $URL"
echo "Press Ctrl-C to stop."

# Open the browser once the server actually answers, not before.
(
  for _ in $(seq 1 60); do
    if curl -sf "$URL/_stcore/health" >/dev/null 2>&1; then
      if command -v open >/dev/null 2>&1; then
        open "$URL"
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1
      fi
      break
    fi
    sleep 0.5
  done
) &

exec "$VENV/bin/streamlit" run app.py --server.port "$PORT"
