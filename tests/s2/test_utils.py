import logging

import pytest
import semver

from flexmeasures_client.s2.utils import get_latest_compatible_version


@pytest.mark.parametrize(
    "supported_versions, current_version, expected_version",
    [
        ([], "2.3.4", "2.3.4"),  # No supported versions → return current_version
        (None, "2.3.4", "2.3.4"),  # No supported versions → return current_version
        (
            ["1.2.3", "1.2.5", "1.2.9"],
            "1.2.4",
            "1.2.9",
        ),  # Pick highest supported (>= 1.2.4)
        (["2.3.0", "2.3.5", "2.3.9"], "2.3.1", "2.3.9"),  # Pick latest >= 2.3.1
        (
            ["1.4.0", "1.3.0", "1.2.3"],
            "1.2.4",
            "1.4.0",
        ),  # Pick highest overall (>= 1.2.4)
        (
            ["1.1.0", "1.2.1"],
            "2.3.4",
            "2.3.4",
        ),  # No compatible version (higher major) → return current_version
        (
            ["2.3.9-alpha", "2.3.5", "2.3.9"],
            "2.3.1",
            "2.3.9",
        ),  # Pre-release ignored, pick latest valid
        (
            ["2.1.0+build123", "2.1.4"],
            "2.1.2",
            "2.1.4",
        ),  # Build metadata ignored, pick latest valid
    ],
)
def test_get_latest_compatible_version(
    supported_versions, current_version, expected_version, caplog
):
    caplog.set_level(logging.WARNING)  # Capture warnings

    latest_version = get_latest_compatible_version(
        supported_versions, current_version, logger=logging.getLogger()
    )

    assert str(latest_version) == expected_version

    if not supported_versions:
        assert "RM didn't provide any supported version" in caplog.text
    elif latest_version == semver.Version.parse(current_version):
        assert "There are no compatible S2 versions" in caplog.text
