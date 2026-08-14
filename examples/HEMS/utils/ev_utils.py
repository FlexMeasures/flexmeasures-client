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

    Build SoC and availability constraints for the next 24 hours.

    Driving is represented by two explicit SoC-usage periods: one after departure
    and one before returning home. Keeping these separate from the SoC minima makes
    rolling rescheduling preserve the remaining part of a trip.
    """
    if capacity_kwh is None:
        capacity_kwh = EV_CONFIG["default_capacity_kwh"]

    print(
        "[EV-CALC] Calculating EV constraints for "
        f"{current_time.strftime('%Y-%m-%d %H:%M')}"
    )
    print(f"  [CAPACITY] Battery capacity: {capacity_kwh} kWh")

    min_soc_kwh = EV_CONFIG["min_soc_percent"] * capacity_kwh
    print(
        f"  [MINIMUM] Minimum SoC: {EV_CONFIG['min_soc_percent']*100:.0f}% "
        f"= {min_soc_kwh:.1f} kWh"
    )

    constraints = {
        "soc_minima": [],
        "soc_usage": [],
        "consumption_capacity": [],
    }
    usage_segments = []
    planning_end = current_time + pd.Timedelta(hours=24)
    commute_duration = pd.Timedelta(hours=EV_CONFIG["one_way_commute_duration_hours"])

    def add_usage_segment(start: pd.Timestamp, end: pd.Timestamp) -> None:
        """Add the part of a driving period that remains in the planning window."""
        start = max(start, current_time)
        end = min(end, planning_end)
        if start < end:
            usage_segments.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "value": f'{EV_CONFIG["driving_consumption_kwh_per_hour"]} kW',
                }
            )

    # Include both the remainder of today's pattern and tomorrow's pattern. This
    # matters when replanning while an EV is away or shortly before tomorrow's
    # departure.
    for day_offset in (0, 1):
        day = current_time.normalize() + pd.Timedelta(days=day_offset)
        needs_charging, departure_time_str, return_time_str, target_soc_percent = (
            get_day_pattern(day)
        )
        target_soc_kwh = target_soc_percent / 100 * capacity_kwh

        if needs_charging and departure_time_str and return_time_str:
            departure_hour, departure_minute = map(int, departure_time_str.split(":"))
            return_hour, return_minute = map(int, return_time_str.split(":"))
            departure_datetime = day.replace(
                hour=departure_hour,
                minute=departure_minute,
                second=0,
                microsecond=0,
            )
            return_datetime = day.replace(
                hour=return_hour,
                minute=return_minute,
                second=0,
                microsecond=0,
            )

            print(
                f"  [WORK-DAY] {day.date()}: {target_soc_percent}% "
                f"({target_soc_kwh:.1f} kWh) by {departure_time_str}; "
                f"return at {return_time_str}"
            )
            if current_time <= departure_datetime <= planning_end:
                constraints["soc_minima"].append(
                    {
                        "datetime": departure_datetime.isoformat(),
                        "value": f"{target_soc_kwh} kWh",
                    }
                )

            unavailable_start = max(departure_datetime, current_time)
            unavailable_end = min(return_datetime, planning_end)
            if unavailable_start < unavailable_end:
                constraints["consumption_capacity"].append(
                    {
                        "start": unavailable_start.isoformat(),
                        "end": unavailable_end.isoformat(),
                        "value": "0 kW",
                    }
                )
                constraints["soc_minima"].append(
                    {
                        "start": unavailable_start.isoformat(),
                        "end": unavailable_end.isoformat(),
                        "value": f"{min_soc_kwh} kWh",
                    }
                )

            add_usage_segment(departure_datetime, departure_datetime + commute_duration)
            add_usage_segment(return_datetime - commute_duration, return_datetime)
        else:
            end_of_day = day + pd.Timedelta(days=1)
            if current_time <= end_of_day <= planning_end:
                constraints["soc_minima"].append(
                    {
                        "datetime": end_of_day.isoformat(),
                        "value": f"{target_soc_kwh} kWh",
                    }
                )
            print(
                f"  [FLEXIBLE-DAY] {day.date()}: maintain "
                f"{target_soc_percent}% ({target_soc_kwh:.1f} kWh)"
            )

    if usage_segments:
        # soc-usage is a list of components; this component is a time series.
        constraints["soc_usage"] = [usage_segments]

    # Handle random trips - reduce SoC randomly to simulate unplanned usage
    if has_random_trip:
        print("  [RANDOM-TRIP] Trip detected!")
        # Random trip consumes configured percentage range of battery
        min_consumption, max_consumption = EV_CONFIG["random_trip_consumption_range"]
        trip_consumption_percent = random.uniform(min_consumption, max_consumption)
        trip_consumption_kwh = trip_consumption_percent * capacity_kwh

        print(
            "    [CONSUMPTION] Trip consumption: "
            f"{trip_consumption_percent*100:.1f}% = "
            f"{trip_consumption_kwh:.1f} kWh"
        )

        # Adjust point-in-time targets to account for the unexpected trip.
        for minimum in constraints["soc_minima"]:
            if "datetime" not in minimum:
                continue
            original_minimum_kwh = float(minimum["value"].split()[0])
            # Ensure we charge enough to cover the trip consumption
            adjusted_minimum = min(
                capacity_kwh, original_minimum_kwh + trip_consumption_kwh
            )
            minimum["value"] = f"{adjusted_minimum} kWh"
            print(
                f"    [ADJUSTED] Minimum: {original_minimum_kwh:.1f} kWh "
                f"-> {adjusted_minimum:.1f} kWh "
                f"(+{trip_consumption_kwh:.1f} kWh for trip)"
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
                    f"    [MINIMUM] {minima['value']} from "
                    f"{start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')}"
                )
    if constraints["consumption_capacity"]:
        for capacity in constraints["consumption_capacity"]:
            start_dt = pd.to_datetime(capacity["start"])
            end_dt = pd.to_datetime(capacity["end"])
            print(
                f"    [DISABLED] Charging: {start_dt.strftime('%H:%M')} "
                f"to {end_dt.strftime('%H:%M')} ({capacity['value']})"
            )
    if usage_segments:
        total_driving_hours = sum(
            (
                pd.Timestamp(segment["end"]) - pd.Timestamp(segment["start"])
            ).total_seconds()
            / 3600
            for segment in usage_segments
        )
        print(
            f"    [DRIVING] {total_driving_hours:.1f} h at "
            f'{EV_CONFIG["driving_consumption_kwh_per_hour"]:.1f} kW'
        )

    print()

    return constraints


def simulate_random_trip() -> bool:
    """Simulate random shopping trips based on configured probability."""
    return random.random() < EV_CONFIG["random_trip_probability"]
