import random

import pandas as pd
from const import EV_CONFIG, EV_WEEKLY_PATTERNS


def get_day_pattern(date_time: pd.Timestamp) -> tuple:
    """Get the EV pattern for a specific day of the week."""
    day_of_week = date_time.weekday()  # Monday = 0, Sunday = 6
    day_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    pattern = EV_WEEKLY_PATTERNS[day_of_week]

    print(f"  [DAY] {day_names[day_of_week]} ({date_time.strftime('%Y-%m-%d')})")
    print(f"  [PATTERN] {pattern}")

    return pattern


def calculate_ev_soc_targets_and_constraints(
    current_time: pd.Timestamp,
    capacity_kwh: float = None,
    has_random_trip: bool = False,
) -> dict:
    """
    Calculate dynamic SoC targets and availability constraints for EV charging.

    Returns a dict with:
    - soc_targets: List of target SoC values with datetimes
    - soc_minima: List of minimum SoC constraints during unavailable periods
    - consumption_capacity: Availability windows (0 during unavailable periods)
    """
    if capacity_kwh is None:
        capacity_kwh = EV_CONFIG["default_capacity_kwh"]

    print(
        f"[EV-CALC] Calculating EV constraints for {current_time.strftime('%Y-%m-%d %H:%M')}"
    )
    print(f"  [CAPACITY] Battery capacity: {capacity_kwh} kWh")

    needs_charging, departure_time_str, return_time_str, target_soc_percent = (
        get_day_pattern(current_time)
    )

    target_soc_kwh = (target_soc_percent / 100.0) * capacity_kwh
    min_soc_kwh = EV_CONFIG["min_soc_percent"] * capacity_kwh

    print(f"  [TARGET] Target SoC: {target_soc_percent}% = {target_soc_kwh:.1f} kWh")
    print(
        f"  [MINIMUM] Minimum SoC: {EV_CONFIG['min_soc_percent']*100:.0f}% = {min_soc_kwh:.1f} kWh"
    )

    constraints = {
        "soc_targets": [],
        "soc_minima": [],
        "consumption_capacity": [],
    }

    if needs_charging and departure_time_str and return_time_str:
        # Work day - need to be charged by departure time
        print(
            f"  [WORK-DAY] Departure at {departure_time_str}, return at {return_time_str}"
        )
        departure_hour, departure_minute = map(int, departure_time_str.split(":"))
        return_hour, return_minute = map(int, return_time_str.split(":"))

        # Target: charged to 80% by departure time
        departure_datetime = current_time.replace(
            hour=departure_hour, minute=departure_minute, second=0, microsecond=0
        )

        # If departure is already past today, target tomorrow
        if departure_datetime <= current_time:
            departure_datetime += pd.Timedelta(days=1)
            print(
                f"  [SCHEDULE] Departure time adjusted to next day: {departure_datetime.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            print(
                f"  [SCHEDULE] Departure time: {departure_datetime.strftime('%Y-%m-%d %H:%M')}"
            )

        constraints["soc_minima"] = [
            {
                "datetime": departure_datetime.isoformat(),
                "value": f"{target_soc_kwh} kWh",
            }
        ]
        print(
            f"  [MINIMUM-SET] SoC minimum set: {target_soc_kwh:.1f} kWh by {departure_datetime.strftime('%H:%M')}"
        )

        # Unavailable period: departure time to return time (same day as departure)
        return_datetime = departure_datetime.replace(
            hour=return_hour, minute=return_minute
        )
        unavailable_duration = return_datetime - departure_datetime

        # Check if we are currently in the unavailable period
        if current_time >= departure_datetime and current_time <= return_datetime:
            print("  [UNAVAILABLE] Currently in unavailable period")
            return_datetime += pd.Timedelta(days=1)
            print(
                f"  [UNAVAILABLE] Period: {departure_datetime.strftime('%H:%M')} - {return_datetime.strftime('%H:%M')} ({unavailable_duration})"
            )
            constraints["unavailable"] = True
        else:
            print(
                f"  [UNAVAILABLE] Period: {departure_datetime.strftime('%H:%M')} - {return_datetime.strftime('%H:%M')} ({unavailable_duration})"
            )

        # Disable charging during unavailable period by setting consumption capacity to 0
        constraints["consumption_capacity"] = [
            {
                "start": departure_datetime.isoformat(),
                "end": return_datetime.isoformat(),
                "value": "0 kW",
            }
        ]

        # Extend minimum SoC constraint during unavailable period
        constraints["soc_minima"].append(
            {
                "start": departure_datetime.isoformat(),
                "end": return_datetime.isoformat(),
                "value": f"{min_soc_kwh} kWh",
            }
        )
        print("  [DISABLED] Charging disabled during unavailable period (0 kW)")
        print(
            f"  [MIN-SOC] Minimum SoC maintained: {min_soc_kwh:.1f} kWh during unavailable period"
        )
    else:
        # Free day - just maintain minimum SoC by end of planning horizon
        print(f"  [FREE-DAY] Flexible charging to {target_soc_percent}%")
        end_of_day = current_time.replace(hour=23, minute=59, second=59, microsecond=0)
        constraints["soc_minima"] = [
            {"datetime": end_of_day.isoformat(), "value": f"{target_soc_kwh} kWh"}
        ]
        print(
            f"  [FLEXIBLE] Minimum: {target_soc_kwh:.1f} kWh by end of day ({end_of_day.strftime('%H:%M')})"
        )
        print("  [AVAILABLE] No availability restrictions - can charge anytime")

    # Handle random trips - reduce SoC randomly to simulate unplanned usage
    if has_random_trip:
        print("  [RANDOM-TRIP] Trip detected!")
        # Random trip consumes configured percentage range of battery
        min_consumption, max_consumption = EV_CONFIG["random_trip_consumption_range"]
        trip_consumption_percent = random.uniform(min_consumption, max_consumption)
        trip_consumption_kwh = trip_consumption_percent * capacity_kwh

        print(
            f"    [CONSUMPTION] Trip consumption: {trip_consumption_percent*100:.1f}% = {trip_consumption_kwh:.1f} kWh"
        )

        # Adjust minima to account for trip consumption
        for minimum in constraints["soc_minima"]:
            original_minimum_kwh = float(minimum["value"].split()[0])
            # Ensure we charge enough to cover the trip consumption
            adjusted_minimum = min(
                capacity_kwh, original_minimum_kwh + trip_consumption_kwh
            )
            minimum["value"] = f"{adjusted_minimum} kWh"
            print(
                f"    [ADJUSTED] Minimum: {original_minimum_kwh:.1f} kWh -> {adjusted_minimum:.1f} kWh (+{trip_consumption_kwh:.1f} kWh for trip)"
            )

    print("  [SUMMARY] Final constraints:")
    if constraints["soc_minima"]:
        for minima in constraints["soc_minima"]:
            if "datetime" in minima:
                # Point-in-time minimum
                dt = pd.to_datetime(minima["datetime"])
                print(
                    f"    [MINIMUM] {minima['value']} by {dt.strftime('%Y-%m-%d %H:%M')}"
                )
            elif "start" in minima and "end" in minima:
                # Period minimum
                start_dt = pd.to_datetime(minima["start"])
                end_dt = pd.to_datetime(minima["end"])
                print(
                    f"    [MINIMUM] {minima['value']} from {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')}"
                )
    if constraints["consumption_capacity"]:
        for capacity in constraints["consumption_capacity"]:
            start_dt = pd.to_datetime(capacity["start"])
            end_dt = pd.to_datetime(capacity["end"])
            print(
                f"    [DISABLED] Charging: {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')} ({capacity['value']})"
            )

    print()

    return constraints


def simulate_random_trip() -> bool:
    """Simulate random shopping trips based on configured probability."""
    return random.random() < EV_CONFIG["random_trip_probability"]
