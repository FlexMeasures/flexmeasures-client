import subprocess

from const import (
    FORECAST_HORIZON_HOURS,
    FORECASTING_START,
    SCHEDULING_END,
    SIMULATION_STEP_HOURS,
    TUTORIAL_START_DATE,
)
from utils.asset_utils import find_sensor_by_name_and_asset

from flexmeasures_client import FlexMeasuresClient


async def generate_forecasts(
    client: FlexMeasuresClient,
    sensor_name: str,
    asset_name: str,
    regressors: list[tuple[str, str]] | None = None,
):
    """Generate forecasts using FlexMeasures CLI for the second week."""
    print(f"Generating {sensor_name} forecasts for {asset_name}...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping forecast generation.")
        return False

    # Find sensors
    target_sensor = await find_sensor_by_name_and_asset(client, sensor_name, asset_name)
    regressor_sensors = []
    if regressors is not None:
        for regressor in regressors:
            regressor_sensor = await find_sensor_by_name_and_asset(
                client, regressor[0], regressor[1]
            )
            regressor_sensors.append(regressor_sensor)

    if not target_sensor:
        print("Could not find required sensors for forecasting")
        return False

    # Run CLI command
    # NOTE: This uses the CLI because there is no public API yet.
    #       An API endpoint is coming soon, so this can later be done via the client.
    #       Requires FlexMeasures PR #1546.
    cmd = [
        "flexmeasures",
        "add",
        "forecasts",
        "--sensor",
        str(target_sensor["id"]),
        "--train-start",
        TUTORIAL_START_DATE,
        "--from-date",
        FORECASTING_START,
        "--to-date",
        SCHEDULING_END,
        "--max-forecast-horizon",
        f"PT{FORECAST_HORIZON_HOURS}H",
        "--forecast-frequency",
        f"PT{SIMULATION_STEP_HOURS}H",
        "--ensure-positive",
        "--model-save-dir",
        "forecaster_models",
    ]

    if regressor_sensors:
        cmd.extend(
            [
                "--past-regressors",
                ",".join([str(sensor["id"]) for sensor in regressor_sensors]),
            ]
        )  # TODO: to be changed to --regressors when the sensor has irradiance forecasts

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode == 0:
        print(f"{sensor_name} forecasts for {asset_name} generated successfully")
        return True
    else:
        print(f"{sensor_name} forecasts for {asset_name} failed: {result.stderr}")
        return False
