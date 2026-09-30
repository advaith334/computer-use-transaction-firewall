#!/bin/sh
# Serve the CRX + update manifest the force-install policy points at, then run the demo.
set -e
cd /app
python3 -m http.server 7700 --bind 127.0.0.1 -d /opt/observ/ext >/dev/null 2>&1 &
if [ "$#" -eq 0 ]; then
  exec uv run --frozen python examples/demo.py
fi
exec uv run --frozen "$@"
