from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
MODULE_PATH = HEMS_DIR / "HEMS_setup.py"

sys.path.insert(0, str(HEMS_DIR))
try:
    module_spec = importlib.util.spec_from_file_location("hems_setup", MODULE_PATH)
    assert module_spec is not None
    assert module_spec.loader is not None
    hems_setup = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(hems_setup)
finally:
    sys.path.remove(str(HEMS_DIR))


def make_state(status="ready"):
    return {
        "status": status,
        "completed-phases": ["asset-setup"],
        "sensor-ids": [1, 2, 3],
    }


@pytest.mark.parametrize(
    "answer, expected",
    [("y", "recreate"), ("w", "wipe"), ("", "resume"), ("n", "resume")],
)
def test_prompt_for_existing_setup_choices(answer, expected):
    with patch("builtins.input", return_value=answer):
        assert (
            hems_setup.prompt_for_existing_setup(
                {"id": 2, "name": "Example"}, "Community", make_state()
            )
            == expected
        )


def test_untracked_setup_cannot_resume():
    with patch("builtins.input", side_effect=["n", "w"]):
        assert (
            hems_setup.prompt_for_existing_setup(
                {"id": 2, "name": "Example"},
                "Community",
                make_state(status="untracked"),
            )
            == "wipe"
        )


def test_data_wipe_requires_exact_confirmation():
    with patch("builtins.input", return_value="wipe"):
        assert hems_setup.confirm_data_wipe(make_state()) is False
    with patch("builtins.input", return_value="WIPE"):
        assert hems_setup.confirm_data_wipe(make_state()) is True


def test_recreation_requires_exact_confirmation():
    account = {"id": 2, "name": "Example"}
    with patch("builtins.input", return_value="recreate"):
        assert hems_setup.confirm_recreation(account, "Community") is False
    with patch("builtins.input", return_value="RECREATE"):
        assert hems_setup.confirm_recreation(account, "Community") is True
