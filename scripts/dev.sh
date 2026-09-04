#!/usr/bin/env bash
# Starts both services and stops both on Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")/.."

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd backend && python3 -m uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &
wait
