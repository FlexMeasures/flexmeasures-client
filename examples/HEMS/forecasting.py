from const import (
    FORECAST_HORIZON_HOURS,
    FORECASTING_START,
    SCHEDULING_END,
    SIMULATION_STEP_HOURS,
    TUTORIAL_START_DATE,
    heating_name,
    pv_name,
    weather_station_name,
)
from utils.asset_utils import find_sensors_by_asset

from flexmeasures_client import FlexMeasuresClient


async def generate_sensor_forecasts(
    client: FlexMeasuresClient,
    sensor_name: str,
    asset_name: str,
    community_name: str,
    regressors: list[tuple[str, str]] | None = None,
):
    """Generate forecasts for the second week and wait until the job finishes."""
    print(f"Generating {sensor_name} forecasts for {asset_name}...")

    # Find sensors
    sensor_mappings = [
        # (key, sensor name, asset name)
        (sensor_name, sensor_name, asset_name),
    ]
    if regressors is not None:
        sensor_mappings.extend(
            [(regressor[0], regressor[0], regressor[1]) for regressor in regressors]
        )
    sensors = await find_sensors_by_asset(
        client=client,
        sensor_mappings=sensor_mappings,
        top_level_asset_name=community_name,
    )
    target_sensor = sensors[sensor_name]
    regressor_sensors = []
    if regressors is not None:
        for regressor in regressors:
            regressor_sensor = sensors[regressor[0]]
            regressor_sensors.append(regressor_sensor)

    if not target_sensor:
        print("Could not find required sensors for forecasting")
        return False


    forecast_id = await client.trigger_forecast(
        sensor_id=target_sensor["id"],
        train_start=TUTORIAL_START_DATE,
        start=FORECASTING_START,
        end=SCHEDULING_END,
        max_forecast_horizon=f"PT{FORECAST_HORIZON_HOURS}H",
        forecast_frequency=f"PT{SIMULATION_STEP_HOURS}H",
        past_regressors=[sensor["id"] for sensor in regressor_sensors]
        if regressor_sensors
        else None,
    )
    if forecast_id is not None:
        print(f"Forecast triggered with ID: {forecast_id}")
        await client.get_forecast(
            sensor_id=target_sensor["id"],
            forecast_id=forecast_id,
        )
        print(f"Forecast job completed for {sensor_name} on {asset_name}")

    return forecast_id


async def generate_forecasts(
    client: FlexMeasuresClient, community_name: str, site_names: list[str,]
):
    """Generate forecasts for sensors that need to be forecasted for tutorial."""

    forecast_configs = []
    for i, site_name in enumerate(site_names, start=1):
        forecast_configs.extend(
            [
                {
                    "community_name": community_name,
                    "asset_name": f"{pv_name} {i}",
                    "sensor_name": "electricity-production",
                    "regressors": [("irradiation", weather_station_name)],
                },
                {
                    "community_name": community_name,
                    "asset_name": site_name,
                    "sensor_name": "electricity-consumption",
                    "regressors": None,
                },
                {
                    "community_name": community_name,
                    "asset_name": f"{heating_name} {i}",
                    "sensor_name": "soc-usage",
                    "regressors": None,
                },
            ]
        )

    for config in forecast_configs:
        await generate_sensor_forecasts(
            client,
            sensor_name=config["sensor_name"],
            asset_name=config["asset_name"],
            community_name=config["community_name"],
            regressors=config.get("regressors", None),
        )
