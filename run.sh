#!/usr/bin/env bash
# Запуск Budsmet локально: ./run.sh [порт]
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8000}"
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" "${@:2}"
