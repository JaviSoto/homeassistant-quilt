#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[smoke] Python syntax check..."
python3 -m py_compile "$ROOT"/custom_components/quilt/*.py

echo "[smoke] OK"
