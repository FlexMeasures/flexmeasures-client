import asyncio
import json
from datetime import timedelta

import pandas as pd
from const import (
    EV_CONFIG,
    FORECAST_HORIZON_HOURS,
    HEATING_CONFIG,
    SCHEDULING_END,
    SCHEDULING_START,
    SIMULATION_STEP_HOURS,
    TUTORIAL_START_DATE,
    battery_name,
    building_name,
    evse1_name,
    evse2_name,
    heating_name,
    price_market_name,
    pv_name,
)
from utils.asset_utils import find_sensor_by_name_and_asset, load_and_align_csv_data
from utils.ev_utils import (
    calculate_ev_soc_targets_and_constraints,
    simulate_random_trip,
)
from utils.scheduling_utils import create_dynamic_storage_flex_model

from flexmeasures_client.client import FlexMeasuresClient


async def run_scheduling_simulation(
    client: FlexMeasuresClient, simulate_live_corrections: bool = True
):
    """Run step-by-step scheduling simulation for the third week with EV charging."""
    print("Running scheduling simulation for third week with EV charging...")

    # Find required assets and sensors
    assets = await client.get_assets(fields=["id", "name", "attributes"])

    # Find building, battery, and EVSE assets
    assets_by_name = {a["name"]: a for a in assets}
    building_asset = assets_by_name.get(building_name)
    battery_asset = assets_by_name.get(battery_name)
    evse1_asset = assets_by_name.get(evse1_name)
    evse2_asset = assets_by_name.get(evse2_name)
    heating_asset = assets_by_name.get(heating_name)

    if not building_asset:
        print("Could not find building asset for scheduling")
        return False

    if not battery_asset:
        print("Could not find battery asset for scheduling")
        return False

    if not evse1_asset or not evse2_asset:
        print("Could not find EVSE assets for scheduling")
        return False
    if not heating_asset:
        print("Could not find heating asset for scheduling")
        return False

    # Find sensors (including EVSE sensors) - using unique keys for duplicate sensor names
    sensor_mappings = [
        ("building-consumption", building_name, "electricity-consumption"),
        ("pv-production", pv_name, "electricity-production"),
        ("battery-power", battery_name, "electricity-power"),
        ("battery-soc", battery_name, "state-of-charge"),
        ("evse1-power", evse1_name, "electricity-power"),
        ("evse1-soc", evse1_name, "state-of-charge"),
        ("evse2-power", evse2_name, "electricity-power"),
        ("evse2-soc", evse2_name, "state-of-charge"),
        ("electricity-price", price_market_name, "electricity-price"),
        ("electricity-aggregate", building_name, "electricity-aggregate"),
        ("heating-power", heating_name, "power"),
        ("heating-soc", heating_name, "state of charge"),
    ]

    sensors = {}
    for sensor_key, asset_name, sensor_name in sensor_mappings:
        sensor = await find_sensor_by_name_and_asset(client, sensor_name, asset_name)
        if sensor:
            sensors[sensor_key] = sensor
        else:
            print(f"Could not find sensor '{sensor_name}' in asset '{asset_name}'")
            return False

    # Load complete datasets for simulation
    building_df = load_and_align_csv_data(
        "data/building_data.csv", TUTORIAL_START_DATE, 15
    )

    # Get battery soc settings
    battery_flex_model = json.loads(battery_asset["attributes"]).get("flex_model")
    if not battery_flex_model:
        print("Battery asset missing flex_model settings")
        return False

    battery_soc_at_start = battery_flex_model.get("soc_at_start")

    # Get EVSE settings
    evse1_flex_model = json.loads(evse1_asset["attributes"]).get("flex_model")
    evse2_flex_model = json.loads(evse2_asset["attributes"]).get("flex_model")
    if not evse1_flex_model or not evse2_flex_model:
        print("EVSE assets missing flex_model settings")
        return False

    evse1_capacity = evse1_flex_model.get(
        "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
    )
    evse2_capacity = evse2_flex_model.get(
        "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
    )

    # Initialize simulation
    current_time = pd.to_datetime(SCHEDULING_START)
    end_time = pd.to_datetime(SCHEDULING_END)
    step_num = 1
    battery_next_current_soc = None
    evse1_next_current_soc = None
    evse2_next_current_soc = None
    heating_next_current_soc = None

    while current_time < end_time:
        print(f"Simulation step {step_num}: {current_time}")

        # Create schedule for the building with battery and EVs
        try:
            schedule_start = current_time
            schedule_duration = timedelta(hours=FORECAST_HORIZON_HOURS)

            # Create flex model for battery
            if battery_next_current_soc is None:
                battery_current_soc = (
                    battery_soc_at_start  # Use initial SoC for first step
                )
            else:
                battery_current_soc = battery_next_current_soc
            # Create dynamic flex model for battery (Current SoC updated each step)
            battery_scheduling_dynamic_flex_model = create_dynamic_storage_flex_model(
                current_soc=battery_current_soc,
            )

            # Calculate dynamic EV constraints for current day
            current_time_ts = pd.Timestamp(current_time)

            print("\n[EVSE-SCHEDULING] === EVSE SCHEDULING CALCULATIONS ===")
            print(
                f"Simulation step {step_num} at {current_time.strftime('%Y-%m-%d %H:%M')}"
            )

            # Simulate random trips for each EVSE
            evse1_has_trip = simulate_random_trip()
            evse2_has_trip = simulate_random_trip()

            print("\n[RANDOM-TRIPS] Trip simulation results:")
            print(f"  EVSE 1: {'Trip scheduled' if evse1_has_trip else 'No trip'}")
            print(f"  EVSE 2: {'Trip scheduled' if evse2_has_trip else 'No trip'}")

            print("\n[EVSE-1] Constraints Calculation:")
            evse1_constraints = calculate_ev_soc_targets_and_constraints(
                current_time_ts, evse1_capacity, evse1_has_trip
            )

            print("[EVSE-2] Constraints Calculation:")
            evse2_constraints = calculate_ev_soc_targets_and_constraints(
                current_time_ts, evse2_capacity, evse2_has_trip
            )
            if not evse1_constraints.get("unavailable"):

                # Create flex models for EVSE 1
                if evse1_next_current_soc is None:
                    # Use initial SoC for first step
                    evse1_current_soc = evse1_flex_model.get("soc_at_start", 12.0)
                else:
                    evse1_current_soc = evse1_next_current_soc
                # Create dynamic flex model for EVSE 1 (Current SoC updated each step)
                evse1_scheduling_dynamic_flex_model = create_dynamic_storage_flex_model(
                    current_soc=evse1_current_soc,
                    constraints=evse1_constraints,
                )

            if not evse2_constraints.get("unavailable"):

                # Create flex models for EVSE 2 (similar pattern, could be different car)
                if evse2_next_current_soc is None:
                    # Use initial SoC for first step
                    evse2_current_soc = evse2_flex_model.get("soc_at_start", 12.0)
                else:
                    evse2_current_soc = evse2_next_current_soc
                # Create dynamic flex model for EVSE 2 (Current SoC updated each step)
                evse2_scheduling_dynamic_flex_model = create_dynamic_storage_flex_model(
                    current_soc=evse2_current_soc,
                    constraints=evse2_constraints,
                )

            if heating_next_current_soc is None:
                # Use initial SoC for first step
                heating_current_soc = (
                    HEATING_CONFIG["soc_at_start_percent"]
                    * HEATING_CONFIG["capacity_kwh"]
                )
            else:
                heating_current_soc = heating_next_current_soc
            # Create dynamic flex model for heating (Current SoC updated each step)
            heating_scheduling_dynamic_flex_model = create_dynamic_storage_flex_model(
                current_soc=heating_current_soc,
            )

            # Start with the battery and PV flex models
            curtailable_pv_flex_model = {
                "power-capacity": "12 kW",
                "consumption-capacity": "0 kW",
                "production-capacity": {"sensor": sensors["pv-production"]["id"]},
            }
            final_flex_models = [
                {
                    "sensor": sensors["battery-power"]["id"],
                    **battery_scheduling_dynamic_flex_model,
                },
                {
                    "sensor": sensors["pv-production"]["id"],
                    **curtailable_pv_flex_model,
                },
                {
                    "sensor": sensors["heating-power"]["id"],
                    **heating_scheduling_dynamic_flex_model,
                },
            ]

            # Conditionally add EVSE flex models if they are not on a trip
            if not evse1_constraints.get("unavailable"):
                final_flex_models.append(
                    {
                        "sensor": sensors["evse1-power"]["id"],
                        **evse1_scheduling_dynamic_flex_model,
                    }
                )
            else:
                print("EVSE 1 is on a trip, skipping scheduling.")

            if not evse2_constraints.get("unavailable"):
                final_flex_models.append(
                    {
                        "sensor": sensors["evse2-power"]["id"],
                        **evse2_scheduling_dynamic_flex_model,
                    }
                )
            else:
                print("EVSE 2 is on a trip, skipping scheduling.")

            print("[FLEX-MODEL-DEBUG] === FLEX MODELS SENT TO SCHEDULER ===")
            for i, model in enumerate(final_flex_models):
                device_name = ["Battery", "PV", "Heating", "EVSE-1", "EVSE-2"][i]
                print(f"[FLEX-MODEL] {device_name}: {model}")
            print()

            # Trigger scheduling and get job UUID to retrieve both power and SoC schedules
            job_uuid = await client.trigger_schedule(
                start=schedule_start,
                duration=schedule_duration,
                flex_model=final_flex_models,
                asset_id=building_asset["id"],
                prior=(
                    current_time + timedelta(hours=SIMULATION_STEP_HOURS)
                    if simulate_live_corrections
                    else current_time
                ),
            )

            print(f"Multi-device scheduling job triggered with UUID: {job_uuid}")

            # Get power schedules for each device
            schedule_result = []
            for flex_model in final_flex_models:
                sensor_id = flex_model["sensor"]
                power_schedule = await client.get_schedule(
                    sensor_id=sensor_id,
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
                power_schedule["sensor"] = sensor_id
                schedule_result.append(power_schedule)

            # Get SoC schedules computed by FlexMeasures using the same job UUID
            # This retrieves the SoC values that FlexMeasures computed based on the flex-model constraints
            try:
                battery_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["battery-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve battery SoC schedule: {e}")
                battery_soc_schedule = {"values": [], "duration": "PT0H"}

            try:
                evse1_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["evse1-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve EVSE1 SoC schedule: {e}")
                evse1_soc_schedule = {"values": [], "duration": "PT0H"}

            try:
                evse2_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["evse2-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve EVSE2 SoC schedule: {e}")
                evse2_soc_schedule = {"values": [], "duration": "PT0H"}

            try:
                heating_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["heating-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve heating SoC schedule: {e}")
                heating_soc_schedule = {"values": [], "duration": "PT0H"}

            print("Multi-device power and SoC schedules retrieved successfully")

        except Exception as e:
            error_msg = str(e)
            print(f"Scheduling failed: {error_msg}")

            # Continue simulation with zero power for all devices
            schedule_result = [
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["battery-power"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["evse1-power"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["evse2-power"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["heating-soc"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
            ]

            # Set empty SoC schedules for error case
            battery_soc_schedule = {"values": [], "duration": "PT0H"}
            evse1_soc_schedule = {"values": [], "duration": "PT0H"}
            evse2_soc_schedule = {"values": [], "duration": "PT0H"}
            heating_soc_schedule = {"values": [], "duration": "PT0H"}

        # Extract scheduled power for all devices for the next 4 hours
        step_end_time = current_time + timedelta(hours=SIMULATION_STEP_HOURS)

        # Initialize power schedules
        battery_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        evse1_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        evse2_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        pv_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        heating_scheduled_power = [0.0] * SIMULATION_STEP_HOURS

        if isinstance(schedule_result, list) and len(schedule_result) >= 3:
            # Extract schedules for each device
            print("[SCHEDULE-DEBUG] === SCHEDULE RESULTS ===")
            for i, schedule in enumerate(schedule_result):
                sensor_id = schedule["sensor"]
                resolution_in_hours = (
                    pd.Timedelta(schedule["duration"])
                    // pd.Timedelta(hours=1)
                    / len(schedule["values"])
                )
                power_values = schedule["values"][
                    : int(SIMULATION_STEP_HOURS / resolution_in_hours)
                ]

                # Find which sensor this is for logging
                sensor_name = "Unknown"
                if sensor_id == sensors["battery-power"]["id"]:
                    battery_scheduled_power = power_values
                    sensor_name = "Battery"
                elif sensor_id == sensors["evse1-power"]["id"]:
                    evse1_scheduled_power = power_values
                    sensor_name = "EVSE-1"
                elif sensor_id == sensors["evse2-power"]["id"]:
                    evse2_scheduled_power = power_values
                    sensor_name = "EVSE-2"
                elif sensor_id == sensors["pv-production"]["id"]:
                    pv_scheduled_power = [-v for v in power_values]
                    sensor_name = "PV"
                elif sensor_id == sensors["heating-power"]["id"]:
                    heating_scheduled_power = power_values
                    sensor_name = "Heating"

                print(
                    f"[SCHEDULE] {sensor_name} (sensor {sensor_id}): {power_values} kW"
                )

            print(f"[SCHEDULE-DEBUG] Current time: {current_time}")
            print(f"[SCHEDULE-DEBUG] Step duration: {SIMULATION_STEP_HOURS} hours")
            print()

        # Upload measurements for the simulation step
        try:
            # Upload battery power measurements
            battery_power_duration = timedelta(hours=SIMULATION_STEP_HOURS)
            await client.post_sensor_data(
                sensor_id=sensors["battery-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=battery_scheduled_power,
                unit="kW",
            )
            # Upload PV power measurements
            await client.post_sensor_data(
                sensor_id=sensors["pv-production"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=pv_scheduled_power,
                unit="kW",
            )

            # Upload EVSE 1 power measurements
            await client.post_sensor_data(
                sensor_id=sensors["evse1-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=evse1_scheduled_power,
                unit="kW",
            )

            # Upload EVSE 2 power measurements
            await client.post_sensor_data(
                sensor_id=sensors["evse2-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=evse2_scheduled_power,
                unit="kW",
            )

            # Upload heating power measurements
            await client.post_sensor_data(
                sensor_id=sensors["heating-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=heating_scheduled_power,
                unit="kW",
            )

            # Upload building consumption for this period
            building_data_step = building_df[
                (building_df["event_start"] >= current_time)
                & (building_df["event_start"] < step_end_time)
            ]

            if not building_data_step.empty:
                step_duration = pd.Timedelta(
                    (
                        building_data_step["event_start"].iloc[-1]
                        - building_data_step["event_start"].iloc[0]
                    )
                    + pd.Timedelta(minutes=15)
                )
                await client.post_sensor_data(
                    sensor_id=sensors["building-consumption"]["id"],
                    start=building_data_step["event_start"].iloc[0],
                    duration=step_duration,
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=building_data_step["event_value"].tolist(),
                    unit="kW",
                )

            # Upload FlexMeasures-computed SoC schedules to sensor data
            # Extract SoC values for the current simulation step
            battery_resolution_in_hours = (
                pd.Timedelta(battery_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(battery_soc_schedule["values"]) - 1)
            )
            evse1_resolution_in_hours = (
                pd.Timedelta(evse1_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(evse1_soc_schedule["values"]) - 1)
            )
            evse2_resolution_in_hours = (
                pd.Timedelta(evse2_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(evse2_soc_schedule["values"]) - 1)
            )
            heating_resolution_in_hours = (
                pd.Timedelta(heating_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(heating_soc_schedule["values"]) - 1)
            )
            battery_soc_values = (
                battery_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / battery_resolution_in_hours)
                ]
                if battery_soc_schedule.get("values")
                else []
            )
            battery_next_current_soc = battery_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / battery_resolution_in_hours)
            ]
            evse1_next_current_soc = evse1_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / evse1_resolution_in_hours)
            ]
            evse2_next_current_soc = evse2_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / evse2_resolution_in_hours)
            ]
            evse1_soc_values = (
                evse1_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / evse1_resolution_in_hours)
                ]
                if evse1_soc_schedule.get("values")
                else []
            )
            evse2_soc_values = (
                evse2_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / evse2_resolution_in_hours)
                ]
                if evse2_soc_schedule.get("values")
                else []
            )
            heating_soc_values = (
                heating_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / heating_resolution_in_hours)
                ]
                if heating_soc_schedule.get("values")
                else []
            )
            heating_next_current_soc = heating_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / heating_resolution_in_hours)
            ]

            # Upload battery SoC measurements (FlexMeasures computed)
            if battery_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["battery-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=battery_soc_values,
                    unit="kWh",
                )
                print(
                    f"[BATTERY-SOC] Uploaded {len(battery_soc_values)} FlexMeasures-computed SoC values"
                )

            # Upload EVSE 1 SoC measurements (FlexMeasures computed)
            if evse1_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["evse1-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=evse1_soc_values,
                    unit="kWh",
                )
                print(
                    f"[EVSE1-SOC] Uploaded {len(evse1_soc_values)} FlexMeasures-computed SoC values"
                )

            # Upload EVSE 2 SoC measurements (FlexMeasures computed)
            if evse2_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["evse2-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=evse2_soc_values,
                    unit="kWh",
                )
                print(
                    f"[EVSE2-SOC] Uploaded {len(evse2_soc_values)} FlexMeasures-computed SoC values"
                )
            # Upload heating SoC measurements (FlexMeasures computed)
            if heating_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["heating-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=heating_soc_values,
                    unit="kWh",
                )
                print(
                    f"[HEATING-SOC] Uploaded {len(heating_soc_values)} FlexMeasures-computed SoC values"
                )
            # Display FlexMeasures-computed SoC and power data
            print("\n[FLEXMEASURES-RESULTS] === FLEX-MODEL COMPUTED SCHEDULES ===")

            # Log power and SoC schedules for this step
            battery_average_power = (
                sum(battery_scheduled_power) / len(battery_scheduled_power)
                if battery_scheduled_power
                else 0
            )
            evse1_average_power = (
                sum(evse1_scheduled_power) / len(evse1_scheduled_power)
                if evse1_scheduled_power
                else 0
            )
            evse2_average_power = (
                sum(evse2_scheduled_power) / len(evse2_scheduled_power)
                if evse2_scheduled_power
                else 0
            )
            heating_average_power = (
                sum(heating_scheduled_power) / len(heating_scheduled_power)
                if heating_scheduled_power
                else 0
            )
            # Show SoC progression if available
            if battery_soc_values:
                battery_soc_start = battery_soc_values[0]
                battery_soc_end = battery_soc_values[-1]
                battery_soc_change = battery_soc_end - battery_soc_start
                print(
                    f"[BATTERY] Power: {battery_average_power:.2f} kW | SoC: {battery_soc_start:.1f} → {battery_soc_end:.1f} kWh ({battery_soc_change:+.1f} kWh)"
                )
            else:
                print(
                    f"[BATTERY] Power: {battery_average_power:.2f} kW | SoC: Not available"
                )

            if evse1_soc_values:
                evse1_soc_start = evse1_soc_values[0]
                evse1_soc_end = evse1_soc_values[-1]
                evse1_soc_change = evse1_soc_end - evse1_soc_start
                evse1_capacity = evse1_flex_model.get(
                    "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
                )
                evse1_percent_end = (evse1_soc_end / evse1_capacity) * 100
                print(
                    f"[EVSE-1] Power: {evse1_average_power:.2f} kW | SoC: {evse1_soc_start:.1f} → {evse1_soc_end:.1f} kWh ({evse1_soc_change:+.1f} kWh, {evse1_percent_end:.1f}%)"
                )
            else:
                print(
                    f"[EVSE-1] Power: {evse1_average_power:.2f} kW | SoC: Not available"
                )

            if evse2_soc_values:
                evse2_soc_start = evse2_soc_values[0]
                evse2_soc_end = evse2_soc_values[-1]
                evse2_soc_change = evse2_soc_end - evse2_soc_start
                evse2_capacity = evse2_flex_model.get(
                    "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
                )
                evse2_percent_end = (evse2_soc_end / evse2_capacity) * 100
                print(
                    f"[EVSE-2] Power: {evse2_average_power:.2f} kW | SoC: {evse2_soc_start:.1f} → {evse2_soc_end:.1f} kWh ({evse2_soc_change:+.1f} kWh, {evse2_percent_end:.1f}%)"
                )
            else:
                print(
                    f"[EVSE-2] Power: {evse2_average_power:.2f} kW | SoC: Not available"
                )
            if heating_soc_values:
                heating_soc_start = heating_soc_values[0]
                heating_soc_end = heating_soc_values[-1]
                heating_soc_change = heating_soc_end - heating_soc_start
                print(
                    f"[HEATING] Power: {heating_average_power:.2f} kW | SoC: {heating_soc_start:.1f} → {heating_soc_end:.1f} kWh ({heating_soc_change:+.1f} kWh)"
                )
            else:
                print(
                    f"[HEATING] Power: {heating_average_power:.2f} kW | SoC: Not available"
                )

        except Exception as e:
            print(f"Failed to upload measurements: {e}")

        # Move to next simulation step
        current_time = step_end_time
        step_num += 1

        print(
            f"\n[STEP-COMPLETE] Step {step_num-1} completed. Next step starts at {current_time.strftime('%Y-%m-%d %H:%M')}"
        )
        print("=" * 80)

        # Add small delay between steps
        await asyncio.sleep(1)

    print("Scheduling simulation completed")
    return True
