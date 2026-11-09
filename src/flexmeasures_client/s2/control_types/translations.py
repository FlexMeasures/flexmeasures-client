from datetime import timedelta

import numpy as np
import pandas as pd

try:
    from s2python.frbc import (
        FRBCFillLevelTargetProfile,
        FRBCLeakageBehaviour,
        FRBCUsageForecast,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )

from flexmeasures_client.s2.control_types.FRBC.utils import element_for_fill_level


def leakage_behaviour_to_storage_efficiency(
    message: FRBCLeakageBehaviour,
    resolution=timedelta(minutes=15),
    fill_level_scale: float = 1,
    fill_level: float | None = None,
    share_of: float | None = None,
) -> float:
    """
    Convert a FRBC.LeakageBehaviour message into a FlexMeasures compatible storage efficiency.

    Definitions:

    LeakageBehaviour: how fast the momentary fill level will decrease per second
    due to leakage within the given range of the fill level. This is defined as a function of the
    fill level.

    Storage Efficiency: percentage of the storage that remains after one time step of the given resolution.

    Example:

    {
        ...,
        "elements" : [
            {
               "fill_level_range" : {"start_of_range" : 0, "end_of_range" : 5},
               "leakage_rate" : 0
            },
            {
               "fill_level_range" : {"start_of_range" : 5, "end_of_range" : 95},
               "leakage_rate" : 1/3600
            }
            {
               "fill_level_range" : {"start_of_range" : 95, "end_of_range" : 100},
               "leakage_rate" : 2/3600
            }
        ]
    }

    """

    # FlexMeasures takes a single storage efficiency here, for the whole window it covers.
    # If the fill level is known, use the element that applies at that fill level, as for a charging efficiency.
    # If not, use the leakiest element.
    # Search for it instead of assuming it comes last: S2 does not fix the order of the elements, and the leakiest one is not always the top one.
    if fill_level is not None:
        element = element_for_fill_level(message.elements, fill_level)
    else:
        element = max(message.elements, key=lambda e: e.leakage_rate)
    max_fill_level = max(
        e.fill_level_range.end_of_range * fill_level_scale for e in message.elements
    )
    leakage = element.leakage_rate * fill_level_scale
    # S2 gives a leakage rate per second.
    # So the share that remains after one second is compounded over the number of seconds in a step.
    # This used to be compounded over the number of hours in a step instead.
    # Take a buffer that loses 1.5 kWh of 10 kWh a day: over twelve hours, it loses about 7%.
    # The old calculation declared that it lost only 0.002% over those twelve hours.
    # Because of that, the scheduler pre-charged the buffer in a cheap hour.
    # FlexMeasures applies a storage efficiency to the current stock, so the loss is a share of a fill level.
    # That fill level is share_of, if given, such as the fill level a schedule starts from.
    # Otherwise, it is the top of the range, which understates the loss at lower fill levels.
    reference = (
        share_of * fill_level_scale
        if share_of is not None and share_of > 0
        else max_fill_level
    )
    seconds_per_step = pd.Timedelta(resolution) / pd.Timedelta(seconds=1)
    storage_efficiency = 100 * max(0.0, 1 - leakage / reference) ** seconds_per_step

    return storage_efficiency


def unevenly_ts_to_evenly(
    start: pd.Timestamp,
    values: list[float],
    durations: list[pd.Timedelta],
    target_resolution: str | timedelta,
    strategy="mean",
) -> pd.Series:
    """
    Convert unevenly spaced time series data into evenly spaced data.

    The function will:
    - Floor the start time to align with the target resolution.
    - Ceil the end time to align with the target resolution.
    - Interpolate and resample the data based on the chosen aggregation strategy.

    Args:
        start (pd.Timestamp): The starting timestamp of the time series data.
        values (list[float]): The list of values for each time period.
        durations (list[pd.Timedelta]): The list of durations for each value.
        target_resolution (str): The target time resolution for resampling.
        strategy (str): Aggregation strategy ("mean", "min", "max", etc.) for resampling.

    Returns:
        pd.Series: A Pandas Series with evenly spaced timestamps and interpolated values.
    """

    # Calculate the time from the absolute start of each event
    deltas = pd.TimedeltaIndex(np.cumsum([timedelta(0)] + durations))

    # Ceil the end time to align with the target resolution
    end = pd.Timestamp(start + deltas[-1]).ceil(target_resolution)

    # Floor the start time to align with the target resolution
    start = start.floor(target_resolution)

    # Create an index for the time series based on the start time and deltas
    index = start + deltas

    # Make a copy of the values list and append a NaN to handle the end boundary
    values = values.copy()
    values.append(np.nan)
    series = pd.Series(values, index)

    # Reindex the series with a regular time grid and forward-fill missing values
    series = series.reindex(
        pd.date_range(
            start=start,
            end=end,
            freq=min(min(durations), pd.Timedelta(target_resolution)),
            inclusive="left",
        )
    ).ffill()

    # Resample the series to the target resolution using the specified aggregation strategy and forward-fill
    series = series.resample(target_resolution).agg(strategy).ffill()

    return series


def translate_usage_forecast_to_fm(
    usage_forecast: FRBCUsageForecast,
    resolution: str | timedelta = "1h",
    strategy: str = "mean",
    fill_level_scale: float = 1,
) -> pd.Series:
    """
    Translate a FRBC.UsageForecast into a FlexMeasures compatible format with evenly spaced data.

    Args:
        usage_forecast (FRBCUsageForecast): The usage forecast message with start time and elements.
        resolution (str): The target time resolution for resampling (e.g., "1h").

    Returns:
        pd.Series: A Pandas Series with evenly spaced timestamps and usage forecast values.
    """

    start = pd.Timestamp(usage_forecast.start_time)

    durations = [element.duration.to_timedelta() for element in usage_forecast.elements]
    values = [
        element.usage_rate_expected * fill_level_scale
        for element in usage_forecast.elements
    ]  # e.g. [0, 1000] fill_level/s -> [0, 100] %/s

    return unevenly_ts_to_evenly(
        start=start,
        values=values,
        durations=durations,
        target_resolution=resolution,
        strategy=strategy,
    )


def translate_fill_level_target_profile(
    fill_level_target_profile: FRBCFillLevelTargetProfile,
    resolution: str | timedelta = "1h",
    fill_level_scale: float = 1,
) -> tuple[pd.Series, pd.Series]:
    """
    Translate a FRBC.FillLevelTargetProfile into SOC minima and maxima compatible with FlexMeasures.

    Args:
        fill_level_target_profile (FRBCFillLevelTargetProfile): The target profile message with start time and elements.
        resolution (str): The target time resolution for resampling (e.g., "1h").

    Returns:
        tuple[pd.Series, pd.Series]: A tuple containing SOC minima and maxima as Pandas Series.
    """

    start = pd.Timestamp(fill_level_target_profile.start_time)

    durations = [
        pd.Timedelta(element.duration.to_timedelta())
        for element in fill_level_target_profile.elements
    ]

    soc_minima_values = [
        element.fill_level_range.start_of_range * fill_level_scale
        for element in fill_level_target_profile.elements
    ]
    soc_maxima_values = [
        element.fill_level_range.end_of_range * fill_level_scale
        for element in fill_level_target_profile.elements
    ]

    soc_minima = unevenly_ts_to_evenly(
        start=start,
        values=soc_minima_values,
        durations=durations,
        target_resolution=resolution,
        strategy="min",
    )

    soc_maxima = unevenly_ts_to_evenly(
        start=start,
        values=soc_maxima_values,
        durations=durations,
        target_resolution=resolution,
        strategy="max",
    )

    return soc_minima, soc_maxima
