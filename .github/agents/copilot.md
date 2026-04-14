---
name: copilot
description: Agent instructions for working on flexmeasures-client
---

# flexmeasures-client Agent Instructions

## Project Overview

`flexmeasures-client` is an async Python client library for connecting to the FlexMeasures API.
The main client class is `FlexMeasuresClient` in `src/flexmeasures_client/client.py`.

## Repository Layout

- `src/flexmeasures_client/` — main package (`client.py` is where all API methods live)
- `tests/` — async tests using `pytest-asyncio` + `aioresponses`
- `docs/` — RST documentation (add new `.rst` files and link them in `docs/index.rst`)

## Running Tests

```bash
uv sync --group test --extra s2
uv run poe test
```

## Linting

Always fix linting before pushing. Run via pre-commit (preferred):

```bash
uv run poe lint
```

Or run individually using `uv run`:

```bash
uv run black src/ tests/
uv run isort src/ tests/
uv run flake8 src/ tests/
```

To install pre-commit as a standalone tool:

```bash
uv tool install pre-commit && pre-commit run --all-files
```

## Coding Patterns

- Add async methods to `client.py` following the existing style.
- Use `await self.request(uri=..., method="GET"/"POST", ...)` for HTTP calls.
- Call `check_for_status(status, expected)` to raise on unexpected status codes.
- Use `pd.Timestamp(x).isoformat()` for datetimes and `pd.Timedelta(x).isoformat()` for durations.
- Pass `minimum_server_version="x.y.z"` when a feature needs a specific server version.
- For endpoints that return 202 (job in progress) → 200 (done), implement polling using `asyncio.sleep` + `async_timeout.timeout` (see `get_forecast()` as a reference).

## Writing Tests

Delegate test writing to the **test-specialist** sub-agent (see `.github/agents/test-specialist.md`).
After the sub-agent completes, **verify yourself** that:

1. All new tests pass: `python3 -m pytest tests/client -q`
2. Linting passes: `black --check src/ tests/ && flake8 src/ tests/`

Do not accept the sub-agent's output at face value — run both checks yourself and iterate if needed.
