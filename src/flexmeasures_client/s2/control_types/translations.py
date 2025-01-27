# flake8: noqa

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


def leakage_behaviour_to_storage_efficieny(
    message: FRBCLeakageBehaviour, resolution=timedelta(minutes=15)
) -> float:
    """
    Convert a FRBC.LeakeageBehaviour message into a FlexMeasures compatible storage efficiency.

    Definitions:

    LeakageBehaviour: how fast the momentary fill level will decrease per second
    due to leakage within the given range of the fill level. This is defined as a function of the
    fill level.

    Storage Efficiency: percentage of the storage that remains after one time period.

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

    last_element = message.elements[-1]
    return (
        1
        - (resolution / timedelta(seconds=1))
        * last_element.leakage_rate
        / last_element.fill_level_range.end_of_range
    )


def unevenly_ts_to_evenly(
    start: pd.Timestamp,
    values: list[float],
    durations: list[pd.Timedelta],
    target_resolution: str,
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
    resolution: str = "1h",
    strategy: str = "mean",
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
    values = [element.usage_rate_expected for element in usage_forecast.elements]

    return unevenly_ts_to_evenly(
        start=start,
        values=values,
        durations=durations,
        target_resolution=resolution,
        strategy=strategy,
    )


def translate_fill_level_target_profile(
    fill_level_target_profile: FRBCFillLevelTargetProfile, resolution: str = "1h"
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
        element.duration.to_timedelta()
        for element in fill_level_target_profile.elements
    ]

    soc_minima_values = [
        element.fill_level_range.start_of_range
        for element in fill_level_target_profile.elements
    ]
    soc_maxima_values = [
        element.fill_level_range.end_of_range
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
