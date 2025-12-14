#!/bin/bash

SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

cd "$SCRIPT_DIR/.." || exit 1
python3 "$SCRIPT_DIR/../app.py"

trap 'exit 0' SIGTERM