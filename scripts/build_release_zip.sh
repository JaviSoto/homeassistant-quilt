#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/quilt.zip}"
if [[ "$OUT" != /* ]]; then
  OUT="$ROOT/$OUT"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/custom_components"
cp -R "$ROOT/custom_components/quilt" "$TMP/custom_components/quilt"

# Exclude local bytecode/cache files from the release artifact.
find "$TMP/custom_components" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$TMP/custom_components" -type f -name '*.pyc' -delete

rm -f "$OUT"
(cd "$TMP" && zip -r -q "$OUT" custom_components)

echo "Wrote $OUT"
