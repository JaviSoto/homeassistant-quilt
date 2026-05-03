# Contributing

Thanks for considering contributing!

## Ground rules

- Don’t commit secrets or captures:
  - Quilt tokens / headers
  - gRPC payload dumps / pcaps / mitm artifacts
  - anything under `**/secrets/`
- Keep changes focused and testable.
- Prefer adding unit tests for protocol parsing/encoding and config flow behavior.
- Agent-owned repo hygiene: if you find the working tree dirty while working
  here, inspect the diff, classify what is valid, obsolete, generated, or
  unrelated, and leave the repository better than you found it. Do not stop with
  “there were pending edits, so I left it uncommitted”; commit and push coherent
  validated changes, discard obvious generated junk, and only escalate when the
  intended semantics are genuinely ambiguous.

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
