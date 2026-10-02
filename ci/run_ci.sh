#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv run --with-requirements requirements-dev.txt coverage run -m pytest -W error
uv run --with-requirements requirements-dev.txt coverage report --fail-under=86
uv run --isolated --with homeassistant==2026.3.0 --with grpcio==1.78.0 python -m scripts.smoke_ha_2026_3

hassfest_source=$(pwd -P)
hassfest_image="ghcr.io/home-assistant/hassfest@sha256:a77f1cf7cfc21ad626ebaae52ecb6131a45ab20223f8c2c0750bfca487aa4f05"
hassfest_remote_host="${HASSFEST_REMOTE_HOST:-miniserver}"
hassfest_staging_root="${HASSFEST_STAGING_ROOT:-$HOME/.cache}"
sshx="${SSHX:-$HOME/.codex/integrations/bin/sshx}"
hassfest_component="quilt"
hassfest_staging=""
hassfest_remote_staging=""
hassfest_remote_staging_candidate=""
cleanup() {
  if [[ -n "$hassfest_remote_staging" ]]; then
    if ! "$sshx" --timeout 20 --connect-timeout 5 --batch \
      "$hassfest_remote_host" -- rm -rf -- "$hassfest_remote_staging" \
      >/dev/null 2>&1; then
      echo "Warning: failed to remove remote hassfest staging $hassfest_remote_staging" >&2
    fi
  fi
  if [[ -n "$hassfest_staging" ]]; then
    rm -rf -- "$hassfest_staging"
  fi
}
trap cleanup EXIT

# Hassfest recursively discovers integrations. Give it only this integration so
# checkout-local environments and unrelated repository files cannot affect it.
mkdir -p "$hassfest_staging_root"
hassfest_staging="$(mktemp -d "$hassfest_staging_root/quilt-hassfest.XXXXXX")"
mkdir -p "$hassfest_staging/custom_components"
rsync -a \
  --exclude '._*' \
  --exclude __pycache__ \
  "$hassfest_source/custom_components/$hassfest_component" \
  "$hassfest_staging/custom_components/"

if [[ "$hassfest_remote_host" == "local" ]]; then
  if ! docker info >/dev/null 2>&1; then
    echo "Local Docker is unavailable for hassfest." >&2
    exit 1
  fi
  docker run --rm --cpus=1 --memory=512m \
    -v "$hassfest_staging:/github/workspace:ro" \
    "$hassfest_image"
elif [[ -x "$sshx" ]] && "$sshx" --timeout 20 --connect-timeout 5 --batch \
  "$hassfest_remote_host" -- docker info >/dev/null 2>&1; then
  hassfest_remote_staging_candidate="$(
    "$sshx" --timeout 20 --connect-timeout 5 --batch \
      "$hassfest_remote_host" -- mktemp -d /tmp/quilt-hassfest.XXXXXX
  )"
  if [[ ! "$hassfest_remote_staging_candidate" =~ ^/tmp/quilt-hassfest\.[[:alnum:]]+$ ]]; then
    echo "Miniserver returned an invalid hassfest staging path." >&2
    exit 1
  fi
  hassfest_remote_staging="$hassfest_remote_staging_candidate"

  # COPYFILE_DISABLE prevents macOS tar from emitting AppleDouble metadata;
  # hassfest reads every staged Python file as UTF-8.
  COPYFILE_DISABLE=1 tar --no-xattrs -C "$hassfest_staging" -cf - custom_components | \
    "$sshx" --stdin --timeout 120 --connect-timeout 5 --batch \
      "$hassfest_remote_host" -- tar -C "$hassfest_remote_staging" -xf -
  "$sshx" --timeout 600 --connect-timeout 5 --batch \
    "$hassfest_remote_host" -- docker run --rm --cpus=1 --memory=512m \
      -v "$hassfest_remote_staging:/github/workspace:ro" \
      "$hassfest_image"
else
  echo "Miniserver Docker is unavailable; set HASSFEST_REMOTE_HOST=local to use local Docker explicitly." >&2
  exit 1
fi
