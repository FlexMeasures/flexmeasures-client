"""Regenerate the HEMS example's illustrative winter solar CSV files.

The values are synthetic, not observations or a production-grade PV model. Solar
elevation sets the daylight envelope; hand-picked cloud-transmission profiles
make the 21 days differ. PV production is derived from the same irradiance series
so the two tutorial sensors do not contradict each other.

Run ``python3 examples/HEMS/generate_solar_data.py`` from the repository root.
"""

import csv
import math
from datetime import datetime, timedelta
from pathlib import Path

from const import TUTORIAL_START_DATE, latitude, longitude

DATA_DIR = Path(__file__).parent / "data"
DAYS = 21
PV_RATED_POWER_KW = 10.0
PV_PERFORMANCE_RATIO = 0.85

# Local clock hours. Intermediate values are linearly interpolated, yielding
# gradual cloud changes while still allowing genuinely uneven production days.
PROFILE_HOURS = (8.0, 9.5, 11.0, 12.5, 14.0, 15.5, 17.0)
CLOUD_PROFILES = {
    "clear": (0.85, 0.91, 0.95, 0.97, 0.94, 0.88, 0.82),
    "thin_cloud": (0.58, 0.62, 0.68, 0.72, 0.65, 0.60, 0.55),
    "overcast": (0.24, 0.31, 0.35, 0.30, 0.25, 0.29, 0.22),
    "broken": (0.75, 0.95, 0.08, 0.90, 0.12, 0.95, 0.65),
    "clearing": (0.20, 0.25, 0.38, 0.78, 0.90, 0.88, 0.82),
    "clouding": (0.86, 0.92, 0.87, 0.70, 0.35, 0.24, 0.20),
    "scattered": (0.80, 0.90, 0.25, 0.85, 0.95, 0.22, 0.75),
    "rain": (0.20, 0.15, 0.12, 0.18, 0.08, 0.22, 0.25),
}
PROFILE_LABELS = {
    "clear": "Clear sky",
    "thin_cloud": "Thin cloud",
    "overcast": "Overcast",
    "broken": "Broken clouds",
    "clearing": "Clearing skies",
    "clouding": "Clouding over",
    "scattered": "Scattered clouds",
    "rain": "Rain clouds",
}

# Days 15 and 16 are the first two scheduling days: one clear, one variable.
DAY_PROFILES = (
    "clear",
    "overcast",
    "broken",
    "thin_cloud",
    "clearing",
    "clear",
    "scattered",
    "clouding",
    "rain",
    "broken",
    "clear",
    "thin_cloud",
    "scattered",
    "overcast",
    "clear",
    "broken",
    "rain",
    "scattered",
    "thin_cloud",
    "clearing",
    "clear",
)


def solar_elevation_sine(at: datetime) -> float:
    """Approximate the sine of solar elevation at an Amsterdam local time."""
    day_of_year = at.timetuple().tm_yday
    b = math.radians(360 * (day_of_year - 81) / 364)
    equation_of_time_minutes = (
        9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    )
    declination = math.radians(
        23.45 * math.sin(math.radians(360 * (284 + day_of_year) / 365))
    )
    utc_offset_hours = at.utcoffset().total_seconds() / 3600
    local_minutes = at.hour * 60 + at.minute + at.second / 60
    solar_minutes = (
        local_minutes + equation_of_time_minutes + 4 * longitude - 60 * utc_offset_hours
    )
    hour_angle = math.radians(solar_minutes / 4 - 180)
    latitude_radians = math.radians(latitude)
    return max(
        0.0,
        math.sin(latitude_radians) * math.sin(declination)
        + math.cos(latitude_radians) * math.cos(declination) * math.cos(hour_angle),
    )


def cloud_transmission(profile_name: str, local_hour: float) -> float:
    """Interpolate the day's cloud factor between its weather-profile knots."""
    profile = CLOUD_PROFILES[profile_name]
    for index in range(len(PROFILE_HOURS) - 1):
        left, right = PROFILE_HOURS[index : index + 2]
        if local_hour <= right:
            fraction = max(0.0, (local_hour - left) / (right - left))
            return profile[index] + fraction * (profile[index + 1] - profile[index])
    return profile[-1]


def hourly_irradiance(start: datetime, profile_name: str) -> float:
    """Average four quarter-hour samples, in W/m², over an hourly event."""
    samples = []
    for minutes in (7.5, 22.5, 37.5, 52.5):
        at = start + timedelta(minutes=minutes)
        elevation = solar_elevation_sine(at)
        local_hour = at.hour + at.minute / 60 + at.second / 3600
        # A simple winter clear-sky envelope, attenuated by the day's clouds.
        samples.append(
            1000 * elevation**0.85 * cloud_transmission(profile_name, local_hour)
        )
    return sum(samples) / len(samples)


def generate_rows() -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Return matching irradiation (W/m²) and PV production (kW) rows."""
    start = datetime.fromisoformat(TUTORIAL_START_DATE)
    irradiation_rows = []
    pv_rows = []
    for day_index, profile_name in enumerate(DAY_PROFILES):
        for hour in range(24):
            event_start = start + timedelta(days=day_index, hours=hour)
            irradiance = round(hourly_irradiance(event_start, profile_name), 2)
            pv_power = round(
                irradiance / 1000 * PV_RATED_POWER_KW * PV_PERFORMANCE_RATIO,
                3,
            )
            irradiation_rows.append((event_start.isoformat(), irradiance))
            pv_rows.append((event_start.isoformat(), pv_power))
    return irradiation_rows, pv_rows


def generate_day_annotations() -> list[dict[str, str]]:
    """Describe each synthetic PV day, with its forecast known at day start."""
    start = datetime.fromisoformat(TUTORIAL_START_DATE)
    annotations = []
    for day_index, profile_name in enumerate(DAY_PROFILES):
        day_start = start + timedelta(days=day_index)
        day_end = day_start + timedelta(days=1)
        annotations.append(
            {
                "content": f"Synthetic PV conditions: {PROFILE_LABELS[profile_name]}",
                "start": day_start.isoformat(),
                "end": day_end.isoformat(),
                "prior": day_start.isoformat(),
            }
        )
    return annotations


def write_csv(path: Path, rows: list[tuple[str, float]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("event_start", "event_value"))
        writer.writerows(rows)


def main() -> None:
    irradiation_rows, pv_rows = generate_rows()
    write_csv(DATA_DIR / "irradiation_data.csv", irradiation_rows)
    write_csv(DATA_DIR / "PV_production_data.csv", pv_rows)


if __name__ == "__main__":
    main()
