"""Check documented Python syntax plus basic-guide calls, arguments, and cleanup."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import flexmeasures_client

DOCS_DIR = Path(__file__).parents[2] / "docs"
PROJECT_DIR = DOCS_DIR.parent
GUIDES = ("forecasting.rst", "scheduling.rst", "reporting.rst")
PYTHON_DOCUMENTS = (
    *(DOCS_DIR / guide for guide in GUIDES),
    PROJECT_DIR / "README.rst",
)
REAL_CLIENT = flexmeasures_client.FlexMeasuresClient


def python_blocks(path: Path) -> list[str]:
    """Extract Python code blocks while removing their directive indentation."""
    matches = re.findall(
        r"\.\. code-block:: python\n"
        r"(?:\n|[ \t]+:[^\n]*\n)*\n"
        r"((?:(?:    ).*(?:\n|$)|\n)+)",
        path.read_text(),
    )
    return [
        "\n".join(
            line[4:] if line.startswith("    ") else line
            for line in match.rstrip().splitlines()
        )
        for match in matches
    ]


@pytest.mark.parametrize("path", PYTHON_DOCUMENTS, ids=lambda path: path.name)
def test_python_blocks_compile(path: Path) -> None:
    """Catch invalid Python in both complete examples and shorter snippets."""
    blocks = python_blocks(path)
    assert blocks
    for block_number, source in enumerate(blocks, start=1):
        compile(
            source,
            f"{path.name}:code-block-{block_number}",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )


class ExampleClient:
    """Small API stand-in used to execute each guide's complete basic example."""

    instances: list[ExampleClient] = []

    def __init__(self, **connection):
        inspect.signature(REAL_CLIENT).bind(**connection)
        self.connection = connection
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.instances.append(self)

    async def trigger_and_get_forecast(self, **kwargs) -> dict:
        inspect.signature(REAL_CLIENT.trigger_and_get_forecast).bind(None, **kwargs)
        self.calls.append(("trigger_and_get_forecast", kwargs))
        return {
            "values": [1.2, 1.5, 1.8],
            "start": "2026-09-05T08:00:00+02:00",
            "duration": "PT24H",
            "unit": "kW",
        }

    async def trigger_and_get_schedule(self, **kwargs) -> dict:
        inspect.signature(REAL_CLIENT.trigger_and_get_schedule).bind(None, **kwargs)
        self.calls.append(("trigger_and_get_schedule", kwargs))
        return {
            "values": [2.0, -1.0],
            "start": "2026-09-05T08:00:00+02:00",
            "duration": "PT12H",
            "unit": "kW",
        }

    async def trigger_and_await_report(self, **kwargs) -> dict:
        inspect.signature(REAL_CLIENT.trigger_and_await_report).bind(None, **kwargs)
        self.calls.append(("trigger_and_await_report", kwargs))
        return {"status": "FINISHED"}

    async def get_sensor_data(self, **kwargs) -> dict:
        inspect.signature(REAL_CLIENT.get_sensor_data).bind(None, **kwargs)
        self.calls.append(("get_sensor_data", kwargs))
        return {
            "values": [3.0],
            "start": "2026-08-17T00:00:00+02:00",
            "duration": "P1D",
            "unit": "MW",
        }

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("guide", "expected_methods"),
    (
        ("forecasting.rst", ["trigger_and_get_forecast"]),
        ("scheduling.rst", ["trigger_and_get_schedule"]),
        (
            "reporting.rst",
            ["trigger_and_await_report", "get_sensor_data"],
        ),
    ),
)
def test_basic_example_runs(
    monkeypatch: pytest.MonkeyPatch, guide: str, expected_methods: list[str]
) -> None:
    """Execute the complete first Python example without requiring a server."""
    ExampleClient.instances = []
    monkeypatch.setattr(flexmeasures_client, "FlexMeasuresClient", ExampleClient)

    source = python_blocks(DOCS_DIR / guide)[0]
    exec(compile(source, guide, "exec"), {"__name__": "__documentation_example__"})

    client = ExampleClient.instances[-1]
    assert [method for method, _ in client.calls] == expected_methods
    assert client.connection == {
        "host": "localhost:5000",
        "ssl": False,
        "email": "user@example.com",
        "password": "password",
    }
    assert client.closed
