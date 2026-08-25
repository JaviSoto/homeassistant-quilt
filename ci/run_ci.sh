#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv run --with-requirements requirements-dev.txt coverage run -m pytest -W error
uv run --with-requirements requirements-dev.txt coverage report --fail-under=86
uv run --isolated --with homeassistant==2026.3.0 --with grpcio==1.78.0 python -m scripts.smoke_ha_2026_3

if ! docker info >/dev/null 2>&1; then
  echo "Docker must be running for the Home Assistant hassfest gate." >&2
  exit 1
fi

mkdir -p "$HOME/.cache"
validation_root="$(mktemp -d "$HOME/.cache/quilt-validation.XXXXXX")"
trap 'rm -rf "$validation_root"' EXIT
rsync -a --exclude .git --exclude .venv "$PWD/" "$validation_root/"

docker run --rm -v "$validation_root:/github/workspace" ghcr.io/home-assistant/hassfest@sha256:a77f1cf7cfc21ad626ebaae52ecb6131a45ab20223f8c2c0750bfca487aa4f05
