---
name: copilot
description: Agent instructions for working on flexmeasures-client
---

# flexmeasures-client Agent Instructions

## Project Overview

`flexmeasures-client` is an async Python client library for connecting to the FlexMeasures API. The main client class is `FlexMeasuresClient` in `src/flexmeasures_client/client.py`.

## Repository Layout

- `src/flexmeasures_client/` — main package
  - `client.py` — main `FlexMeasuresClient` class (all API methods live here)
  - `response_handling.py` — HTTP response checks, polling logic
  - `constants.py` — API version, content-type headers
  - `exceptions.py` — custom exception classes
- `tests/` — async tests using `pytest-asyncio` + `aioresponses`

## Running Tests

```bash
pip install -e ".[testing]"
python3 -m pytest tests/test_client.py -q
```

All tests must pass (`35+` depending on features added). Never run only the full suite; use targeted file-level tests first.

## Linting and Code Quality

The project enforces linting via `.pre-commit-config.yaml`. **Always run these checks and fix any issues before committing or requesting review.**

### Linting tools (run individually when pre-commit is unavailable)

```bash
pip install black isort flake8

# Format (black)
black src/flexmeasures_client/ tests/

# Sort imports (isort)
isort src/flexmeasures_client/ tests/

# Style check (flake8)
flake8 src/flexmeasures_client/ tests/
```

### Running via pre-commit

```bash
pip install pre-commit
pre-commit run --all-files
```

**Fix all linting issues before pushing. Do not leave black reformatting errors.**

## Coding Patterns

### Adding a new API method to `FlexMeasuresClient`

1. Add the async method to `client.py` following the existing style.
2. Use `await self.request(uri=..., method="GET"/"POST", ...)` for all HTTP calls.
3. Call `check_for_status(status, expected)` to raise on unexpected status codes.
4. Use `pd.Timestamp(x).isoformat()` for datetime params and `pd.Timedelta(x).isoformat()` for duration params.
5. Pass `minimum_server_version="x.y.z"` when a feature requires a specific server version.

### Polling endpoints (202 → 200)

When an endpoint returns 202 while a job is running and 200 when complete, implement polling directly using `asyncio.sleep` and `async_timeout.timeout`, similar to `get_forecast()`.

### Key response-handling rules (in `response_handling.py`)

- `<300` → pass
- `303` → redirect
- `400` with "Scheduling job waiting/in progress" → poll
- `401` → re-authenticate once
- `503` + `Retry-After` → poll

The `request()` method in `FlexMeasuresClient` already handles standard retries; only implement custom polling for endpoints that return `202`.

## Test Patterns

See `.github/agents/test-specialist.md` for full test-writing guidelines.

Key points:
- Use `@pytest.mark.asyncio` + `async def test_*() -> None:`
- Use `aioresponses` to mock HTTP calls
- Set `flexmeasures_client.access_token = "test-token"` to skip auth
- Always `await flexmeasures_client.close()` at the end of each test
- Use short `request_timeout=2, polling_interval=0.2` for polling tests

## API Version and Server Compatibility

- Client uses API version `v3_0` by default (`constants.py`)
- New endpoints requiring FlexMeasures ≥ 0.31.0 must pass `minimum_server_version="0.31.0"` to `request()`

## Forecasting Endpoints (added in v0.31.0)

- `POST /sensors/{id}/forecasts/trigger` → `trigger_forecast(sensor_id, start, end, duration, ...)`
  - Top-level keys: `start`, `end`, `duration`, `max-forecast-horizon`, `forecast-frequency`, `probabilistic`
  - Nested `config` dict keys: `train-start`, `train-period`, `max-training-period`, `retrain-frequency`, `future-regressors`, `past-regressors`, `regressors`
  - Returns forecast job UUID from `response["forecast"]`
- `GET /sensors/{id}/forecasts/{uuid}` → `get_forecast(sensor_id, forecast_id)`
  - Returns 202 while job is running; 200 with `{values, start, duration, unit}` when done
- `trigger_and_get_forecast(sensor_id, ...)` combines both
