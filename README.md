# Home Assistant — Quilt (Unofficial)

Unofficial Home Assistant integration for Quilt HVAC.

## Features

- **Climate** entities for each Quilt space (mode + setpoint + current temperature).
- Optional **real-time-ish updates** via Quilt’s notifier stream, with polling fallback.
- **Light** entities for the thermostat/dial LEDs:
  - brightness + RGB color
  - animation/effects (Sparkle Fade / Twinkle Fade / Dance / Chase)
- **Fan** entities for each space (kept HA-only by default; see HomeKit notes).

## Installation

This is a normal HACS custom integration repository.

Maintainer note: I have been using this integration in my own Home Assistant setup for a couple of months, and it has been working great.

- **HACS (Custom repository)**: add this repo as a custom integration repo in HACS, then install “Quilt”.
  - HACS → Integrations → (⋮) → **Custom repositories** → add `JaviSoto/homeassistant-quilt` as category **Integration**.
- **Manual**: copy `custom_components/quilt/` into your Home Assistant `/config/custom_components/quilt/`, then restart HA Core.

## Setup

- In Home Assistant: **Settings → Devices & Services → Add Integration → Quilt**
- Enter your email, then the verification code Quilt emails you.

## Energy usage

- Exposes kWh **sensor** entities per space so you can graph usage.
- The “Energy last 7 days” sensor includes hourly bucket data in attributes (mirrors what the Quilt app shows).

## HomeKit notes

- HomeKit does **not** support “light effects” controls, so Quilt light animations will remain HA-only.
- HomeKit does not provide a clean way to expose fan speed/mode *inside* a thermostat tile. If you expose the fan entity, it will show up as a separate HomeKit accessory/tile.

## Disclaimer

See `DISCLAIMER.md`.

## Limitations

- Not affiliated with Quilt; no official support.
- Some advanced HVAC features may not map perfectly to HA/HomeKit UX (for example, fan speed/mode).
- This project was vibe coded with OpenAI Codex. Please treat it as an unofficial community integration and verify behavior in your environment before relying on it for critical automations.

## Development

- Run unit tests + coverage:
  - `uv run --with-requirements requirements-dev.txt -- coverage run -m pytest`
  - `uv run --with-requirements requirements-dev.txt -- coverage report --fail-under=85`

## Issues

- Use GitHub Issues for bug reports and feature requests.
- Please include logs, exact repro steps, and your Home Assistant version when reporting bugs.
