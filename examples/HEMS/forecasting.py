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
from synthetic_weather import generate_weather_forecasts
from utils.asset_utils import (
    find_sensors_by_asset,
    post_sensor_data_and_track_ingestion,
    upload_csv_file_to_sensor,
    wait_for_ingestion_jobs,
)

from flexmeasures_client import FlexMeasuresClient


async def generate_sensor_forecasts(
    client: FlexMeasuresClient,
    sensor_name: str,
    asset_name: str,
    community_name: str,
    regressors: list[tuple[str, str]] | None = None,
    future_regressors: list[tuple[str, str]] | None = None,
) -> str | None:
    """
    Generate rolling forecasts for the simulation and wait until the job finishes.
    Return the forecast job ID, or None if the target sensor is missing.
    """
    print(f"Generating {sensor_name} forecasts for {asset_name}...")

    # Find sensors
    sensor_mappings = [
        # (key, sensor name, asset name)
        (sensor_name, sensor_name, asset_name),
    ]
    for regressor_name, regressor_asset in (regressors or []) + (
        future_regressors or []
    ):
        sensor_mappings.append((regressor_name, regressor_name, regressor_asset))
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
        return None

    forecast_id: str | None = None
    try:
        forecast_id = await client.trigger_forecast(
            sensor_id=target_sensor["id"],
            train_start=TUTORIAL_START_DATE,
            start=FORECASTING_START,
            end=SCHEDULING_END,
            max_forecast_horizon=f"PT{FORECAST_HORIZON_HOURS}H",
            forecast_frequency=f"PT{SIMULATION_STEP_HOURS}H",
            past_regressors=(
                [sensor["id"] for sensor in regressor_sensors]
                if regressor_sensors
                else None
            ),
            future_regressors=(
                [sensors[name]["id"] for name, _ in future_regressors]
                if future_regressors
                else None
            ),
        )
        print(f"Forecast triggered with ID: {forecast_id}")
        await client.get_forecast(
            sensor_id=target_sensor["id"],
            forecast_id=forecast_id,
        )
    except Exception as exc:
        job_id = forecast_id if forecast_id is not None else "unknown"
        raise RuntimeError(
            f"Forecast job {job_id} failed for {sensor_name} on " f"{asset_name}: {exc}"
        ) from exc

    print(f"Forecast job completed for {sensor_name} on {asset_name}")

    return forecast_id


async def generate_forecasts(
    client: FlexMeasuresClient, community_name: str, site_names: list[str,]
):
    """Generate forecasts for sensors that need to be forecasted for tutorial."""

    # The v1.1.1 pipeline trains future covariates on realized history and uses
    # forecasts ahead. Forecast-only history would be discarded and filled,
    # leaving the model unable to learn the irradiation-to-PV relationship.
    weather = await find_sensors_by_asset(
        client,
        [("weather", "irradiation-forecast", weather_station_name)],
        community_name,
    )
    pending_ingestion_jobs: list[str] = []
    await upload_csv_file_to_sensor(
        client=client,
        sensor_id=weather["weather"]["id"],
        file_path="data/irradiation_data.csv",
        belief_time_measured_instantly=True,
        pending_ingestion_jobs=pending_ingestion_jobs,
    )
    for forecast in generate_weather_forecasts():
        await post_sensor_data_and_track_ingestion(
            client=client,
            pending_ingestion_jobs=pending_ingestion_jobs,
            sensor_id=weather["weather"]["id"],
            await_ingestion=False,
            **forecast,
        )
    await wait_for_ingestion_jobs(client, pending_ingestion_jobs)

    forecast_configs = []
    for i, site_name in enumerate(site_names, start=1):
        forecast_configs.extend(
            [
                {
                    "community_name": community_name,
                    "asset_name": f"{pv_name} {i}",
                    "sensor_name": "electricity-production",
                    "regressors": [("irradiation", weather_station_name)],
                    "future_regressors": [
                        ("irradiation-forecast", weather_station_name)
                    ],
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
            future_regressors=config.get("future_regressors"),
        )
