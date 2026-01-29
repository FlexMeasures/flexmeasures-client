import subprocess

from const import (
    SCHEDULING_END,
    SCHEDULING_START,
    battery_name,
    evse1_name,
    evse2_name,
    heating_name,
    price_market_name,
    pv_name,
)
from utils.asset_utils import find_sensors_by_asset
from utils.reporter_utils import fill_reporter_params, run_report_cmd

from flexmeasures_client import FlexMeasuresClient


async def create_reports(
    client: FlexMeasuresClient, community_name: str, site_names: list[str]
):
    """Generate reports using FlexMeasures CLI."""
    print("Generating reports...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping report generation.")
        return False
    for i, site_name in enumerate(site_names, start=1):

        # Find all required sensors
        sensor_mappings = [
            ("electricity-production", "electricity-production", f"{pv_name} {i}"),
            ("pv-power", "electricity-power", f"{pv_name} {i}"),
            ("electricity-consumption", "electricity-consumption", site_name),
            ("electricity-power", "electricity-power", f"{battery_name} {i}"),
            ("evse1-power", "electricity-power", f"{evse1_name} {i}"),
            ("evse2-power", "electricity-power", f"{evse2_name} {i}"),
            ("electricity-aggregate", "electricity-aggregate", site_name),
            ("self-consumption", "self-consumption", site_name),
            ("electricity-price", "electricity-price", price_market_name),
            ("total-energy-costs", "total-energy-costs", site_name),
            ("daily-total-energy-costs", "daily-total-energy-costs", site_name),
            (
                "daily-share-of-self-consumption",
                "daily-share-of-self-consumption",
                site_name,
            ),
            ("heating-power", "power", f"{heating_name} {i}"),
        ]
        sensors = await find_sensors_by_asset(
            client, sensor_mappings, top_level_asset_name=community_name
        )

        # Prepare parameters for self-consumption reporter
        fill_reporter_params(
            input_sensors=[
                {"production": sensors["electricity-production"]["id"]},
                {"pv-power": sensors["pv-power"]["id"]},
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

    return self_consumption_result and total_energy_costs_result
