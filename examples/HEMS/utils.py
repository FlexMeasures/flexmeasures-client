import json
import subprocess


def fill_reporter_params(
    input_sensors: list[dict],
    output_sensors: list[dict] | dict,
    start: str,
    end: str,
    reporter_type: str,
):
    """Fill reporter parameters and save to JSON file."""

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
    with open(f"configs/{reporter_type}_reporter_param.json", "w") as f:
        json.dump(params, f, indent=4)


def run_report_cmd(reporter_map: dict, start: str, end: str) -> bool:
    """Run subprocess command for report and print result."""

    cmd = [
        "flexmeasures",
        "add",
        "report",
        "--reporter",
        reporter_map["reporter"],
        "--config",
        f"configs/{reporter_map['name']}_reporter_config.json",
        "--parameters",
        f"configs/{reporter_map['name']}_reporter_param.json",
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
