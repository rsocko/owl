#!/bin/sh
# Ensure /app/data is writable by appuser regardless of volume ownership.
# Runs as root, fixes permissions, then drops to appuser for the actual process.
set -e

# Fix ownership on the data volume (may have been created by a prior root container)
if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /app/data 2>/dev/null || true
    exec gosu appuser "$@"
else
    # Already running as non-root (e.g., docker run --user)
    exec "$@"
fi
