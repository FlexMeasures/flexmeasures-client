import json
import os
from pathlib import Path

from flexmeasures_client import FlexMeasuresClient
from flexmeasures_client.exceptions import (
    ContentTypeError,
    InsufficientServerVersionError,
    JobFailedError,
    JobTimeoutError,
)

BASE_DIR = Path(__file__).parent.parent

# Errors that mean "this report did not happen", as opposed to a bug in the example.
REPORT_ERRORS = (
    JobFailedError,
    JobTimeoutError,
    InsufficientServerVersionError,
    ContentTypeError,
    ConnectionError,
    ValueError,
)


def load_reporter_config(reporter_type: str) -> dict:
    """
    Load a reporter configuration from the `configs/` directory.

    Configurations are static, hand-written data, kept as JSON for readability:

        configs/{reporter_type}_reporter_config.json

    They are read here on the client side and posted to the API as part of the
    report request, so the server never needs access to these files.
    """
    full_path = os.path.join(BASE_DIR, f"configs/{reporter_type}_reporter_config.json")
    with open(full_path) as f:
        return json.load(f)


def build_reporter_parameters(
    input_sensors: list[dict],
    output_sensors: list[dict] | dict,
    start: str,
    end: str,
    reporter_type: str,
) -> dict:
    """
    Build the reporter parameters for a single report.

    :param input_sensors: list of single-entry dicts, mapping the input name the
                          reporter config expects to a sensor ID.
    :param output_sensors: list of sensors to write to. The aggregate reporter
                           takes a single sensor instead, written without a name.
    """
    if reporter_type == "aggregate":
        # For the aggregate reporter, output_sensors is a single sensor
        output = [{"sensor": output_sensors["id"]}]
    else:
        output = [{"name": s["name"], "sensor": s["id"]} for s in output_sensors]

    return {
        "input": [
            {
                "name": name,
                "sensor": sensor,
                "exclude_source_types": ["scheduler", "forecaster"],
            }
            for sensor_dict in input_sensors
            for name, sensor in sensor_dict.items()
        ],
        "output": output,
        "start": start,
        "end": end,
        "belief_horizon": "PT0H",  # Live reporting; reports on measurements straight away (no lag)
        "check_output_resolution": False,
    }


def asset_id_for_outputs(output_sensors: list[dict] | dict) -> int:
    """
    The asset to trigger a report against: the one owning its output sensors.

    The API requires every output sensor to sit in the subtree of the asset in
    the URL, so a report on site sensors is triggered against that site, and a
    report on community sensors against the community.
    """
    sensors = [output_sensors] if isinstance(output_sensors, dict) else output_sensors
    asset_ids = {sensor["generic_asset_id"] for sensor in sensors}
    if len(asset_ids) != 1:
        raise ValueError(
            f"Expected all output sensors to belong to one asset, but got {asset_ids}. "
            "Split this into one report per asset."
        )
    return asset_ids.pop()


async def run_report(
    client: FlexMeasuresClient,
    reporter: str,
    reporter_type: str,
    input_sensors: list[dict],
    output_sensors: list[dict] | dict,
    start: str,
    end: str,
) -> bool:
    """
    Run a single report through the API and wait for its job to finish.

    Requires the server to run a worker on the `reporting` queue:

        flexmeasures jobs run-worker --queue reporting

    :param reporter: FlexMeasures reporter class name, e.g. "PandasReporter".
    :param reporter_type: name of the reporter in this example, used to find its
                          configuration file, e.g. "self-consumption".

    :returns: True if the report finished, False if it failed or timed out.
    """
    asset_id = asset_id_for_outputs(output_sensors)
    parameters = build_reporter_parameters(
        input_sensors=input_sensors,
        output_sensors=output_sensors,
        start=start,
        end=end,
        reporter_type=reporter_type,
    )
    print(f"Running {reporter_type} report on asset {asset_id}...")
    try:
        await client.trigger_and_await_report(
            asset_id=asset_id,
            reporter=reporter,
            parameters=parameters,
            config=load_reporter_config(reporter_type),
        )
    except REPORT_ERRORS as exception:
        print(f"{reporter_type} report failed: {exception}")
        return False
    print(f"{reporter_type} report generated successfully")
    return True
