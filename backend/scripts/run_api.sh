#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
UV_CACHE_DIR=backend/.uv-cache uv run --project backend uvicorn backend.app.main:app --reload
