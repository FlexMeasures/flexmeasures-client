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
from utils.reporter_utils import run_report

from flexmeasures_client import FlexMeasuresClient


async def create_reports(
    client: FlexMeasuresClient, community_name: str, site_names: list[str]
):
    """Generate reports through the FlexMeasures API.

    Each site gets a self-consumption report and a total energy costs report,
    triggered against that site's asset. The latter reads the aggregate power
    written by the aggregate reporter, so it has to run after those.
    """
    print("Generating reports...")

    all_reports_succeeded = True
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

        # Run SelfConsumptionReporter
        self_consumption_result = await run_report(
            client=client,
            reporter="PandasReporter",
            reporter_type="self-consumption",
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
        )

        # Run TotalEnergyCostsReporter
        total_energy_costs_result = await run_report(
            client=client,
            reporter="PandasReporter",
            reporter_type="total-energy-costs",
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
        )

        all_reports_succeeded = (
            self_consumption_result
            and total_energy_costs_result
            and all_reports_succeeded
        )

    return all_reports_succeeded
