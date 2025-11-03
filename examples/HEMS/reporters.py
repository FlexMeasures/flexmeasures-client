import subprocess

from const import (
    SCHEDULING_END,
    SCHEDULING_START,
    battery_name,
    building_name,
    evse1_name,
    evse2_name,
    heating_name,
    price_market_name,
    pv_name,
)
from utils.asset_utils import find_sensors_by_asset
from utils.reporter_utils import fill_reporter_params, run_report_cmd

from flexmeasures_client import FlexMeasuresClient


async def create_reports(client: FlexMeasuresClient):
    """Generate reports using FlexMeasures CLI."""
    print("Generating reports...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping report generation.")
        return False

    # Find all required sensors
    sensor_mappings = [
        ("electricity-production", "electricity-production", pv_name),
        ("electricity-consumption", "electricity-consumption", building_name),
        ("electricity-power", "electricity-power", battery_name),
        ("evse1-power", "electricity-power", evse1_name),
        ("evse2-power", "electricity-power", evse2_name),
        ("electricity-aggregate", "electricity-aggregate", building_name),
        ("self-consumption", "self-consumption", building_name),
        ("electricity-price", "electricity-price", price_market_name),
        ("total-energy-costs", "total-energy-costs", building_name),
        ("daily-total-energy-costs", "daily-total-energy-costs", building_name),
        (
            "daily-share-of-self-consumption",
            "daily-share-of-self-consumption",
            building_name,
        ),
        ("heating-power", "power", heating_name),
    ]
    sensors = await find_sensors_by_asset(client, sensor_mappings)

    # Prepare parameters for the aggregate reporter
    fill_reporter_params(
        input_sensors=[
            {"pv": sensors["electricity-production"]["id"]},
            {"consumption": sensors["electricity-consumption"]["id"]},
            {"battery-power": sensors["electricity-power"]["id"]},
            {"evse1-power": sensors["evse1-power"]["id"]},
            {"evse2-power": sensors["evse2-power"]["id"]},
            {"heating-power": sensors["heating-power"]["id"]},
        ],
        output_sensors=sensors["electricity-aggregate"],
        start=SCHEDULING_START,
        end=SCHEDULING_END,
        reporter_type="aggregate",
    )

    # Prepare parameters for self-consumption reporter
    fill_reporter_params(
        input_sensors=[
            {"production": sensors["electricity-production"]["id"]},
            {"building-consumption": sensors["electricity-consumption"]["id"]},
            {"evse1-consumption": sensors["evse1-power"]["id"]},
            {"evse2-consumption": sensors["evse2-power"]["id"]},
            {"battery-power": sensors["electricity-power"]["id"]},
            {"heating-power": sensors["heating-power"]["id"]},
        ],
        output_sensors=[
            sensors["self-consumption"],
            sensors["daily-share-of-self-consumption"],
        ],
        start=SCHEDULING_START,
        end=SCHEDULING_END,
        reporter_type="self-consumption",
    )

    # Prepare parameters for the total energy costs reporter
    fill_reporter_params(
        input_sensors=[
            {"aggregate-power": sensors["electricity-aggregate"]["id"]},
            {"consumption-production-price": sensors["electricity-price"]["id"]},
            {"heating-power": sensors["heating-power"]["id"]},
        ],
        output_sensors=[
            sensors["total-energy-costs"],
            sensors["daily-total-energy-costs"],
        ],
        start=SCHEDULING_START,
        end=SCHEDULING_END,
        reporter_type="total-energy-costs",
    )

    # Run AggregatorReporter
    aggregate_result = run_report_cmd(
        reporter_map={"name": "aggregate", "reporter": "AggregatorReporter"},
        start=SCHEDULING_START,
        end=SCHEDULING_END,
    )

    # Run SelfConsumptionReporter
    self_consumption_result = run_report_cmd(
        reporter_map={"name": "self-consumption", "reporter": "PandasReporter"},
        start=SCHEDULING_START,
        end=SCHEDULING_END,
    )

    # Run TotalEnergyCostsReporter
    total_energy_costs_result = run_report_cmd(
        reporter_map={"name": "total-energy-costs", "reporter": "PandasReporter"},
        start=SCHEDULING_START,
        end=SCHEDULING_END,
    )

    return self_consumption_result and aggregate_result and total_energy_costs_result
