import json
import os
import shlex
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


def cli_command_prefix() -> list[str]:
    """
    The command used to invoke the FlexMeasures CLI, split into argv tokens.

    Defaults to a plain ``flexmeasures`` on PATH, which requires the CLI to be
    installed locally and configured to talk to the same database as the
    server the client is scripting against (see the "Running the Tutorial"
    instructions in docs/HEMS.rst).

    Override via the ``FLEXMEASURES_CLI_CMD`` environment variable to run the
    CLI elsewhere, e.g. inside a Docker Compose service:

        FLEXMEASURES_CLI_CMD="docker compose -f /path/to/docker-compose.yml exec -T server flexmeasures"
    """
    return shlex.split(os.environ.get("FLEXMEASURES_CLI_CMD", "flexmeasures"))


def _cli_config_path(local_path: str) -> str:
    """
    Translate a local config/parameter file path to the path the CLI process
    will see it at, when that process runs somewhere other than this host
    (e.g. inside a container with the ``configs/`` directory bind-mounted
    elsewhere). Controlled via ``FLEXMEASURES_CLI_CONFIG_DIR``; a no-op if unset.
    """
    remote_dir = os.environ.get("FLEXMEASURES_CLI_CONFIG_DIR")
    if not remote_dir:
        return local_path
    return os.path.join(remote_dir, os.path.basename(local_path))


def fill_reporter_params(
    input_sensors: list[dict],
    output_sensors: list[dict] | dict,
    start: str,
    end: str,
    reporter_type: str,
):
    """
    Fill reporter parameters and save them to a JSON configuration file.

    The file is saved inside the `configs/` directory, with the name
    derived from the given `reporter_type`, e.g.: configs/{reporter_type}_config.json
    """

    if reporter_type == "aggregate":
        # For the aggregate reporter, output_sensors is a single sensor ID
        output = [{"sensor": output_sensors["id"]}]
    else:
        output = [{"name": s["name"], "sensor": s["id"]} for s in output_sensors]

    params = {
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

    # overwrite the file (creates it if not exists)
    file_path = f"configs/{reporter_type}_reporter_param.json"
    full_path = os.path.join(BASE_DIR, file_path)
    with open(full_path, "w") as f:
        json.dump(params, f, indent=4)


def run_report_cmd(reporter_map: dict, start: str, end: str) -> bool:
    """
    Run the FlexMeasures CLI command to generate a report for a given reporter.

    This function expects the reporter configuration and parameter files
    to already exist in the `configs/` directory, following the naming pattern
    created by `fill_reporter_params()`:

        configs/{reporter_name}_reporter_config.json
        configs/{reporter_name}_reporter_param.json

    Args:
        reporter_map (dict): A dictionary describing the reporter to run.
            Must contain:
                - "name": str  → name of the reporter, used in file paths
                - "reporter": str  → FlexMeasures reporter class name
            Example:
                reporter_map = {
                    "name": "aggregate",
                    "reporter": "AggregatorReporter",
                }

        start (str): Start time of the report period (ISO 8601 format).
        end (str): End time of the report period (ISO 8601 format).
    """
    config_path = os.path.join(
        BASE_DIR, f"configs/{reporter_map['name']}_reporter_config.json"
    )
    param_path = os.path.join(
        BASE_DIR, f"configs/{reporter_map['name']}_reporter_param.json"
    )
    cmd = [
        *cli_command_prefix(),
        "add",
        "report",
        "--reporter",
        reporter_map["reporter"],
        "--config",
        _cli_config_path(config_path),
        "--parameters",
        _cli_config_path(param_path),
        "--start",
        start,
        "--end",
        end,
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3000)
    if result.returncode == 0:
        print(f"{reporter_map['name']} reporters generated successfully")
        return True
    else:
        print(f"{reporter_map['name']} reporter generation failed: {result.stderr}")
        return False
