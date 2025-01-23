import pytest
from s2python.frbc import FRBCUsageForecast

from flexmeasures_client.s2.control_types.translations import (
    translate_usage_forecast_to_fm,
)
from flexmeasures_client.s2.utils import get_unique_id


@pytest.mark.parametrize(
    "start, resolution, values",
    [
        ("2024-01-01T00:00:00+01:00", "1h", [100, 100]),
        ("2024-01-01T00:00:00+01:00", "15min", [100] * 4 * 2),
        ("2024-01-01T00:30:00+01:00", "1h", [100, 100, 100]),
    ],
)
def test_resampling_one_block(start, resolution, values):
    message = {
        "elements": [
            {"duration": 2 * 3600 * 1e3, "usage_rate_expected": 100},
        ],
        "message_id": get_unique_id(),
        "message_type": "FRBC.UsageForecast",
        "start_time": start,
    }

    usage_forecast = FRBCUsageForecast.from_dict(message)

    s = translate_usage_forecast_to_fm(usage_forecast, resolution=resolution)
    assert all(abs(s.values - values) < 1e-5)


@pytest.mark.parametrize(
    "start, resolution, values",
    [
        ("2024-01-01T00:00:00+01:00", "1h", [100, 200, 200, 350, 450, 600]),
        ("2024-01-01T00:45:00+01:00", "1h", [100, 200, 200, 350, 450, 600, 600]),
        (
            "2024-01-01T00:00:00+01:00",
            "30min",
            [100] * 2 + [200] * 2 * 2 + [300] * 1 + [400] * 2 + [500] * 1 + [600] * 1,
        ),
        (
            "2024-01-01T00:00:00+01:00",
            "15min",
            [100] * 4 + [200] * 4 * 2 + [300] * 2 + [400] * 4 + [500] * 2 + [600] * 2,
        ),
    ],
)
def test_usage_forecast(start, resolution, values):
    """
    - 100 for 1h
    - 200 for 2h
    - 300 for 30min
    - 400 for 1h
    - 500 for 30min
    - 600 for 30min

    """
    message = {
        "elements": [
            {"duration": 3600 * 1e3, "usage_rate_expected": 100},
            {"duration": 2 * 3600 * 1e3, "usage_rate_expected": 200},
            {"duration": 0.5 * 3600 * 1e3, "usage_rate_expected": 300},
            {"duration": 3600 * 1e3, "usage_rate_expected": 400},
            {"duration": 0.5 * 3600 * 1e3, "usage_rate_expected": 500},
            {"duration": 0.5 * 3600 * 1e3, "usage_rate_expected": 600},
        ],
        "message_id": get_unique_id(),
        "message_type": "FRBC.UsageForecast",
        "start_time": start,
    }

    usage_forecast = FRBCUsageForecast.from_dict(message)

    s = translate_usage_forecast_to_fm(usage_forecast, resolution=resolution)
    assert all(abs(s.values - values) < 1e-5)
