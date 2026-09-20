#!/usr/bin/env bash
# Double-clickable launcher for macOS. Finder runs this in Terminal.
# The real logic lives in run.sh so there is only one script to maintain.
cd "$(dirname "$0")"
./run.sh "$@"
status=$?
if [ $status -ne 0 ]; then
  # Finder closes the window on exit, so hold it open long enough to read
  # whatever went wrong.
  echo
  echo "ShafferFinEval exited with status $status."
  read -r -p "Press Return to close this window. "
fi
exit $status
