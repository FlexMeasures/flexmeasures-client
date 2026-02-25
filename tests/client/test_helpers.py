from __future__ import annotations

import pytest

from flexmeasures_client.client import FlexMeasuresClient


def test_convert_units_mw_to_w():
    result = FlexMeasuresClient.convert_units([1.0], "MW", "W")
    assert result == [1_000_000.0]


def test_convert_units_mw_to_kw():
    result = FlexMeasuresClient.convert_units([1.0], "MW", "kW")
    assert result == [1000.0]


def test_convert_units_kw_to_w():
    result = FlexMeasuresClient.convert_units([1.0], "kW", "W")
    assert result == [1000.0]


def test_convert_units_same():
    result = FlexMeasuresClient.convert_units([1.0, 2.0], "MW", "MW")
    assert result == [1.0, 2.0]


def test_convert_units_w_to_kw():
    result = FlexMeasuresClient.convert_units([1000.0], "W", "kW")
    assert result == [1.0]


def test_convert_units_kw_to_mw():
    result = FlexMeasuresClient.convert_units([1000.0], "kW", "MW")
    assert result == [1.0]


def test_convert_units_w_to_mw():
    result = FlexMeasuresClient.convert_units([1_000_000.0], "W", "MW")
    assert result == [1.0]


def test_convert_units_unsupported():
    with pytest.raises(NotImplementedError):
        FlexMeasuresClient.convert_units([1.0], "MW", "GW")


def test_create_storage_flex_model_optional_params():
    result = FlexMeasuresClient.create_storage_flex_model(
        soc_unit="kWh",
        soc_at_start=50,
        soc_max=400,
        soc_min=20,
        roundtrip_efficiency=0.9,
        storage_efficiency=0.95,
        soc_minima=[{"datetime": "2023-01-01T00:00+00:00", "value": 10}],
        soc_maxima=[{"datetime": "2023-01-01T00:00+00:00", "value": 390}],
    )
    assert result["soc-max"] == 400
    assert result["soc-min"] == 20
    assert result["roundtrip-efficiency"] == 0.9
    assert result["storage-efficiency"] == 0.95
    assert result["soc-minima"] is not None
    assert result["soc-maxima"] is not None


def test_create_storage_flex_context_optional_params():
    result = FlexMeasuresClient.create_storage_flex_context(
        consumption_price_sensor=1,
        production_price_sensor=2,
        inflexible_device_sensors=[3, 4],
    )
    assert result["consumption-price-sensor"] == 1
    assert result["production-price-sensor"] == 2
    assert result["inflexible-device-sensors"] == [3, 4]
