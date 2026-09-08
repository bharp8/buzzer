#!/bin/bash
# Start the buzzer app with GPIO backend (Raspberry Pi)

# cd to script's directory (works regardless of where run.sh is called from)
cd "$(dirname "$(readlink -f "$0")")" || exit 1

# Set GPIO backend
export BUZZER_BACKEND=gpio

# Activate venv if present
if [ -d ".venv/bin" ]; then
    source .venv/bin/activate
fi

# Run uvicorn; exec replaces the shell so systemd tracks the correct PID
exec uvicorn buzzer.app:app --host 0.0.0.0 --port "${BUZZER_PORT:-8000}"
