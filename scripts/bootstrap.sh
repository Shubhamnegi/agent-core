#!/usr/bin/env bash
set -euo pipefail

uv venv .venv
source .venv/bin/activate
uv sync

echo "Environment ready. Activate with: source .venv/bin/activate"
