#!/usr/bin/env bash
# Gear360 Hub - Linux/macOS dev runner (sandbox/CI)
cd "$(dirname "$0")"
HUB_HOST="${HUB_HOST:-127.0.0.1}"
HUB_PORT="${HUB_PORT:-8765}"
exec python3 -m uvicorn app.main:app --host "$HUB_HOST" --port "$HUB_PORT"
