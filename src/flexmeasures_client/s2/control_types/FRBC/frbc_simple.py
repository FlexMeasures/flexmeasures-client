"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

from datetime import datetime, timedelta

import pytz

import pandas as pd

try:
    from s2python.frbc import (
        FRBCActuatorStatus,
        FRBCFillLevelTargetProfile,
        FRBCStorageStatus,
        FRBCSystemDescription,
        FRBCUsageForecast,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2.control_types.FRBC import FRBC
from flexmeasures_client.s2.control_types.FRBC.utils import (
    fm_schedule_to_instructions,
    get_soc_min_max,
)
from flexmeasures_client.s2.control_types.translations import (
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)


class FRBCSimple(FRBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _soc_sensor_id: int
    _rm_discharge_sensor_id: int
    _soc_minima_sensor_id: int
    _soc_maxima_sensor_id: int
    _usage_forecast_sensor_id: int
    _schedule_duration: timedelta
    _fill_level_scale: int = 1
    _resolution = "15min"

    def __init__(
        self,
        power_sensor_id: int,
        soc_sensor_id: int,
        rm_discharge_sensor_id: int,
        price_sensor_id: int,
        soc_minima_sensor_id: int,
        soc_maxima_sensor_id: int,
        usage_forecast_sensor_id: int,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        max_size: int = 100,
        power_unit: str = "kW",
        energy_unit: str = "kWh",
    ) -> None:
        super().__init__(max_size)
        self._power_sensor_id = power_sensor_id
        self._price_sensor_id = price_sensor_id
        self._schedule_duration = schedule_duration
        self._soc_sensor_id = soc_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id
        self._soc_minima_sensor_id = soc_minima_sensor_id
        self._soc_maxima_sensor_id = soc_maxima_sensor_id
        self._usage_forecast_sensor_id = usage_forecast_sensor_id
        self._timezone = pytz.timezone(timezone)
        self.power_unit = power_unit
        self.energy_unit = energy_unit

    def now(self):
        return self._timezone.localize(datetime.now())

    async def send_storage_status(self, status: FRBCStorageStatus):
        await self._fm_client.post_sensor_data(
            self._soc_sensor_id,
            start=self.now(),
            values=[status.present_fill_level],
            unit=self.energy_unit,
            duration=timedelta(minutes=1),
        )
        await self.trigger_schedule()

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        factor = status.operation_mode_factor
        sd: FRBCSystemDescription = list(self._system_description_history.values())[-1]
        fill_rate = sd.actuators[0].operation_modes[0].elements[0].fill_rate

        power = (
            fill_rate.start_of_range
            + (fill_rate.end_of_range - fill_rate.start_of_range) * factor
        )

        start = status.transition_timestamp or self.now()

        await self._fm_client.post_sensor_data(
            self._power_sensor_id,
            start=start,
            values=[-power],
            unit=self.power_unit,
            duration=timedelta(minutes=15),
        )

    async def trigger_schedule(self, system_description_id: str | None = None):
        """Translates S2 System Description into FM API calls"""

        if system_description_id:
            system_description: FRBCSystemDescription = (
                self._system_description_history[system_description_id]
            )
        else:
            # Use last SystemDescription
            system_description: FRBCSystemDescription = list(
                self._system_description_history.values()
            )[-1]
        system_descriptions = self._system_description_history.values()
        self._logger.error(
            list(
                [
                    system_description.valid_from
                    for system_description in system_descriptions
                ]
            )
        )
        self._logger.debug(f"Using system description: {system_description}")

        if len(self._storage_status_history) > 0:
            soc_at_start = list(self._storage_status_history.values())[
                -1
            ].present_fill_level
        else:
            print("Can't trigger schedule without knowing the status of the storage...")
            return

        soc_min, soc_max = get_soc_min_max(system_description)

        # call schedule
        start = system_description.valid_from  # TODO: localize datetime
        start = start.replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)
        schedule = await self._fm_client.trigger_and_get_schedule(
            start=start,
            sensor_id=self._power_sensor_id,
            flex_context={
                "production-price": {"sensor": self._price_sensor_id},
                "consumption-price": {"sensor": self._price_sensor_id},
                "site-power-capacity": f"{3 * 25 * 230} VA",
                "relax-constraints": True,
            },
            flex_model={
                "soc-unit": self.energy_unit,
                "soc-at-start": soc_at_start,  # TODO: use forecast of the SOC instead
                "soc-min": soc_min,
                "soc-max": soc_max,
                "soc-minima": {"sensor": self._soc_minima_sensor_id},
                "soc-maxima": {"sensor": self._soc_maxima_sensor_id},
                "state-of-charge": {"sensor": self._soc_sensor_id},
                "soc-usage": [{"sensor": self._usage_forecast_sensor_id}],
            },
            duration=self._schedule_duration,  # next 12 hours
            # TODO: add SOC MAX AND SOC MIN FROM fill_level_range,
            # this needs changes on the client
        )

        # translate FlexMeasures schedule into instructions. SOC -> Power -> PowerFactor
        instructions = fm_schedule_to_instructions(
            schedule, system_description, initial_fill_level=soc_at_start
        )

        # put instructions to sending queue
        for instruction in instructions:
            await self.send_message(instruction)

    async def send_fill_level_target_profile(
        self, fill_level_target_profile: FRBCFillLevelTargetProfile
    ):
        """
        Send FRBC.FillLevelTargetProfile to FlexMeasures.

        Args:
            fill_level_target_profile (FRBCFillLevelTargetProfile): The fill level target profile to be translated and sent.
        """
        # if not self._is_timer_due("fill_level_target_profile"):
        #     return

        soc_minima, soc_maxima = translate_fill_level_target_profile(
            fill_level_target_profile=fill_level_target_profile,
            resolution=self._resolution,
            fill_level_scale=self._fill_level_scale,
        )

        duration = str(pd.Timedelta(self._resolution) * len(soc_maxima))

        # POST SOC Minima measurements to FlexMeasures
        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_minima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_minima.tolist(),
            unit=self.energy_unit,
            duration=duration,
        )

        # POST SOC Maxima measurements to FlexMeasures
        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_maxima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_maxima.tolist(),
            unit=self.energy_unit,
            duration=duration,
        )

    async def send_usage_forecast(self, usage_forecast: FRBCUsageForecast):
        """
        Send FRBC.UsageForecast to FlexMeasures.

        Args:
            usage_forecast (FRBCUsageForecast): The usage forecast to be translated and sent.
        """
        # if not self._is_timer_due("usage_forecast"):
        #     return

        start_time = usage_forecast.start_time

        # flooring to previous 15min tick
        start_time = start_time.replace(
            minute=(start_time.minute // 15) * 15, second=0, microsecond=0
        )

        usage_forecast = translate_usage_forecast_to_fm(
            usage_forecast,
            self._resolution,
            strategy="mean",
            fill_level_scale=self._fill_level_scale,
        )

        # Scale usage forecast e.g. [0, 100] %/s ->  [0, 100] %/(15 min)
        scale = timedelta(minutes=15) / timedelta(seconds=1)
        scaled_usage_forecast = usage_forecast * scale

        await self._fm_client.post_sensor_data(
            sensor_id=self._usage_forecast_sensor_id,
            start=start_time,
            values=scaled_usage_forecast.tolist(),
            unit=self.power_unit,  # e.g. [0, 100] MW/(15 min)
            duration=str(pd.Timedelta(self._resolution) * len(usage_forecast)),
        )
