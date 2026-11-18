"""Imperfect, reproducible weather forecasts for the synthetic HEMS world.

These are illustrative forecasts, not a weather service. Like the measurements,
they use the synthetic day's cloud profile, but smooth and shift its clouds and
add an intensity bias. These errors shrink as the forecast lead time decreases.
The irradiation-forecast sensor also receives verifying measurements, known
only at each event's end, so the PV model can train on realized weather history.
"""

import math
from datetime import datetime, timedelta

from const import SIMULATION_STEP_HOURS, TUTORIAL_START_DATE
from generate_solar_data import (
    CLOUD_PROFILES,
    DAY_PROFILES,
    cloud_transmission,
    solar_elevation_sine,
)


def generate_weather_forecasts() -> list[dict]:
    """Issue 48 hourly values each simulation step, including historical vintages.

    Start an issuance one day before the data begins, so the first events
    already have a forecast. Clip at the measured series' boundaries.
    A belief time (prior) identifies each issuance; newer forecasts must not be
    visible to a PV forecast made before that time.
    """
    start = datetime.fromisoformat(TUTORIAL_START_DATE)
    end = start + timedelta(days=len(DAY_PROFILES))
    step = timedelta(hours=SIMULATION_STEP_HOURS)
    if step <= timedelta(0):
        raise ValueError("SIMULATION_STEP_HOURS must be positive")
    forecasts = []
    issued_at = start - timedelta(days=1)
    while issued_at < end:
        event_start = max(start, issued_at)
        forecast_end = min(end, issued_at + timedelta(hours=48))
        values = []
        at = event_start
        while at < forecast_end:
            day_index = (at - start).days
            profile_name = DAY_PROFILES[day_index]
            mean_cloud = sum(CLOUD_PROFILES[profile_name]) / len(
                CLOUD_PROFILES[profile_name]
            )
            lead_hours = (at - issued_at).total_seconds() / 3600
            # A stable error pattern per target day makes successive revisions
            # converge towards the synthetic weather rather than adding fresh
            # random errors. Retain some error even at a zero-hour lead.
            error_scale = 0.15 + 0.85 * lead_hours / 48
            timing_error = 0.8 * math.sin(day_index * 0.7 + 1)
            intensity_bias = 1 + 0.18 * math.sin(day_index * 0.9 + 0.4)
            samples = []
            for minutes in (7.5, 22.5, 37.5, 52.5):
                sample_at = at + timedelta(minutes=minutes)
                hour = sample_at.hour + sample_at.minute / 60 + sample_at.second / 3600
                imperfect_cloud = (
                    0.8 * cloud_transmission(profile_name, hour + timing_error)
                    + 0.2 * mean_cloud
                )
                imperfect_cloud = max(0.0, min(1.0, imperfect_cloud * intensity_bias))
                actual_cloud = cloud_transmission(profile_name, hour)
                cloud = actual_cloud + error_scale * (imperfect_cloud - actual_cloud)
                samples.append(1000 * solar_elevation_sine(sample_at) ** 0.85 * cloud)
            values.append(round(sum(samples) / len(samples), 2))
            at += timedelta(hours=1)
        forecasts.append(
            dict(
                start=event_start,
                duration=forecast_end - event_start,
                values=values,
                prior=issued_at,
                unit="W/m²",
            )
        )
        issued_at += step
    return forecasts
