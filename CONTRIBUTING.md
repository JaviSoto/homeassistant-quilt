# Contributing

Thanks for considering contributing!

## Ground rules

- Don’t commit secrets or captures:
  - Quilt tokens / headers
  - gRPC payload dumps / pcaps / mitm artifacts
  - anything under `**/secrets/`
- Keep changes focused and testable.
- Prefer adding unit tests for protocol parsing/encoding and config flow behavior.

## Development setup

This repo intentionally avoids depending on the full Home Assistant Python tree for unit tests.

### Run tests + coverage

Using `uv` (recommended):

```bash
uv run --with-requirements requirements-dev.txt -- coverage run -m pytest
uv run --with-requirements requirements-dev.txt -- coverage report --fail-under=85
```

### Local smoke check

```bash
./scripts/smoke_local.sh
```

## Licensing

This project is released under the MIT license.
See `LICENSE` for details.
