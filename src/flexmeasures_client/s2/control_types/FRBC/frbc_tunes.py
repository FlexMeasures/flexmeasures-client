# flake8: noqa
"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

import asyncio
from datetime import datetime, timedelta
from requests.exceptions import HTTPError

import pandas as pd

"""
Import optional dependency needed for timezone aware datetimes to avoid
a blocking call to import_module in an async loop (e.g. from HomeAssistant)
Error:  Detected blocking call to import_module with args ('tzdata',)
"""
from pandas.compat._optional import import_optional_dependency

import_optional_dependency("tzdata")


from typing import cast

import pydantic
import pytz

try:
    from s2python.common import NumberRange, ReceptionStatus, ReceptionStatusValues
    from s2python.frbc import (
        FRBCActuatorStatus,
        FRBCFillLevelTargetProfile,
        FRBCLeakageBehaviour,
        FRBCStorageStatus,
        FRBCSystemDescription,
        FRBCUsageForecast,
    )

    from flexmeasures_client.s2.const import FILL_LEVEL_SCALE
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2 import register
from flexmeasures_client.s2.control_types.FRBC import FRBC
from flexmeasures_client.s2.control_types.FRBC.utils import fm_schedule_to_instructions
from flexmeasures_client.s2.control_types.translations import (
    leakage_behaviour_to_storage_efficieny,
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)
from flexmeasures_client.s2.utils import get_reception_status, get_unique_id

RESOLUTION = "15min"
ENERGY_UNIT = "MWh"
POWER_UNIT = "MW"
DIMENSIONLESS = "dimensionless"
PERCENTAGE = "%"
TASK_PERIOD_SECONDS = 1 * 60
CONVERSION_EFFICIENCY_DURATION = f"PT{24 * 31}H"


class FillRateBasedControlTUNES(FRBC):
    _fill_level_sensor_id: int

    _fill_rate_sensor_id: int
    _thp_fill_rate_sensor_id: int
    _thp_efficiency_sensor_id: int
    _nes_fill_rate_sensor_id: int
    _nes_efficiency_sensor_id: int
    _leakage_behaviour_sensor_id: int

    _active_actuator_id_sensor_id: int

    _schedule_duration: timedelta

    _usage_forecast_sensor_id: int
    _soc_minima_sensor_id: int
    _soc_maxima_sensor_id: int
    _state_of_charge_sensor_id: int

    _consumption_price_sensor_id: int
    _production_price_sensor_id: int

    _timers: dict[str, datetime]
    _minimum_measurement_period: timedelta = timedelta(minutes=5)

    def __init__(
        self,
        soc_minima_sensor_id: int,
        soc_maxima_sensor_id: int,
        fill_level_sensor_id: int,
        usage_forecast_sensor_id: int,
        thp_fill_rate_sensor_id: int,
        thp_efficiency_sensor_id: int,
        nes_fill_rate_sensor_id: int,
        nes_efficiency_sensor_id: int,
        fill_rate_sensor_id: int,
        rm_discharge_sensor_id: int,
        active_actuator_id_sensor_id: int,
        leakage_beaviour_sensor_id: int,
        production_price_sensor: int,
        consumption_price_sensor: int,
        state_of_charge_sensor_id: int,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
        timers: dict[str: datetime] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(max_size)

        self._fill_level_sensor_id = fill_level_sensor_id

        self._fill_rate_sensor_id = fill_rate_sensor_id
        self._thp_fill_rate_sensor_id = thp_fill_rate_sensor_id
        self._thp_efficiency_sensor_id = thp_efficiency_sensor_id
        self._nes_fill_rate_sensor_id = nes_fill_rate_sensor_id
        self._nes_efficiency_sensor_id = nes_efficiency_sensor_id
        self._leakage_behaviour_sensor_id = leakage_beaviour_sensor_id

        self._active_actuator_id_sensor_id = active_actuator_id_sensor_id

        self._schedule_duration = schedule_duration

        self._usage_forecast_sensor_id = usage_forecast_sensor_id
        self._soc_minima_sensor_id = soc_minima_sensor_id
        self._soc_maxima_sensor_id = soc_maxima_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id
        self._state_of_charge_sensor_id = state_of_charge_sensor_id

        self._consumption_price_sensor_id = consumption_price_sensor
        self._production_price_sensor_id = production_price_sensor

        self._timezone = pytz.timezone(timezone)

        # delay the start of the schedule from the time `valid_from` of the FRBC.SystemDescription
        self._valid_from_shift = valid_from_shift

        self._active_recurring_schedule = False
        self._timers = dict()

        self.last_system_description_hash: int = 0

        self._timers = timers if timers is not None else {}

    def is_timer_due(self, name: str):
        now = datetime.now()
        due_time = self._timers.get(name, now - self._minimum_measurement_period)
        if due_time <= now:
            self._timers[name] = now + self._minimum_measurement_period
            return True
        else:
            self._logger.debug(f"Timer for {name} is not due until {self._timers[name]}")
            return False

    def now(self):
        return datetime.now(self._timezone)

    async def send_storage_status(self, status: FRBCStorageStatus):
        if not self.is_timer_due("storage_status"):
            return

        try:
            await self._fm_client.post_sensor_data(
                self._fill_level_sensor_id,
                start=self.now(),
                values=[status.present_fill_level * FILL_LEVEL_SCALE],
                unit=ENERGY_UNIT,
                duration=timedelta(minutes=0),  # INSTANTANEOUS
            )
        except Exception as e:
            response = ReceptionStatus(
                subject_message_id=status.message_id,
                status=ReceptionStatusValues.PERMANENT_ERROR,
            )
            await self._sending_queue.put(response)

    async def send_leakage_behaviour(self, leakage: FRBCLeakageBehaviour):
        if not self.is_timer_due("leakage_behaviour"):
            return

        try:
            start = self.now()
            start = start.replace(
                minute=(start.minute // 15) * 15, second=0, microsecond=0
            )

            await self._fm_client.post_sensor_data(
                self._leakage_behaviour_sensor_id,
                start=start,
                values=[
                    leakage_behaviour_to_storage_efficieny(
                        message=leakage, resolution=timedelta(minutes=15)
                    )
                ],
                unit=PERCENTAGE,
                duration=timedelta(hours=48),
            )
        except Exception as e:
            response = ReceptionStatus(
                subject_message_id=leakage.message_id,
                status=ReceptionStatusValues.PERMANENT_ERROR,
            )
            await self._sending_queue.put(response)

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        if not self.is_timer_due("actuator_status"):
            return

        factor = status.operation_mode_factor
        system_description: FRBCSystemDescription = list(
            self._system_description_history.values()
        )[-1]

        # find the active FRBCOperationMode
        for op_pos, operation_mode in enumerate(
            system_description.actuators[0].operation_modes
        ):
            if operation_mode.id == status.active_operation_mode_id:
                break

        dt = status.transition_timestamp  # self.now()

        # Assume that THP is op_pos = 0 and NES = op_pos = 1.
        # TODO: should we rely on a sensor_id? For example, "nes-actuator-mode", "thp-actuator-mode"
        if op_pos == 0:
            active_operation_mode_fill_rate_sensor_id = self._thp_fill_rate_sensor_id
        else:
            active_operation_mode_fill_rate_sensor_id = self._nes_fill_rate_sensor_id

        # Operation Mode Factor to fill rate
        fill_rate = operation_mode.elements[0].fill_rate
        fill_rate = (
            fill_rate.start_of_range
            + (fill_rate.end_of_range - fill_rate.start_of_range) * factor
        ) * FILL_LEVEL_SCALE

        # Send data to the sensor of the fill rate corresponding to the active operation mode
        await self._fm_client.post_sensor_data(
            sensor_id=active_operation_mode_fill_rate_sensor_id,
            start=dt,
            values=[fill_rate],
            unit=POWER_UNIT,
            duration=timedelta(minutes=15),
        )

        # Send data to the sensor of the input fill_rate to the storage device
        await self._fm_client.post_sensor_data(
            sensor_id=self._fill_rate_sensor_id,
            start=dt,
            values=[fill_rate],
            unit=POWER_UNIT,
            duration=timedelta(minutes=15),
        )

        await self._fm_client.post_sensor_data(
            sensor_id=self._active_actuator_id_sensor_id,
            start=dt,
            values=[active_operation_mode_fill_rate_sensor_id],
            unit="dimensionless",
            duration=timedelta(minutes=15),
        )

    async def start_trigger_schedule(self):
        """
        Start a recurring task to create new schedules.

        This function ensures that the scheduling task is started only once.
        """

        if not self._active_recurring_schedule:
            self._active_recurring_schedule = True
            self._recurrent_task = asyncio.create_task(self.trigger_schedule_task())
            self.background_tasks.add(
                self._recurrent_task
            )  # important to avoid a task disappearing mid-execution.
            self._recurrent_task.add_done_callback(self.background_tasks.discard)

    async def stop_trigger_schedule(self):
        """
        Stop the recurring task that creates new schedules.

        This function ensures that the scheduling task is stopped gracefully.
        """

        if self._active_recurring_schedule:
            self._active_recurring_schedule = False
            self._recurrent_task.cancel()

    async def trigger_schedule_task(self):
        """
        Recurring task to trigger the schedule creation process.

        This task runs continuously while the active recurring schedule is enabled.
        """

        while self._active_recurring_schedule:
            await self.trigger_schedule()
            await asyncio.sleep(TASK_PERIOD_SECONDS)

    async def trigger_schedule(self):
        """
        Ask FlexMeasures for a new schedule and create FRBC.Instructions to send back to the ResourceManager
        """

        # Retrieve the latest system description from history
        system_description: FRBCSystemDescription = list(
            self._system_description_history.values()
        )[-1]

        actuator = system_description.actuators[0]
        fill_level_range: NumberRange = system_description.storage.fill_level_range

        # get SOC Max and Min to be sent on the Flex Model
        soc_min = fill_level_range.start_of_range * FILL_LEVEL_SCALE
        soc_max = fill_level_range.end_of_range * FILL_LEVEL_SCALE

        operation_mode = None
        for _operation_mode in actuator.operation_modes:
            if "THP" in _operation_mode.diagnostic_label:
                operation_mode = _operation_mode
            elif "NES" in _operation_mode.diagnostic_label:
                operation_mode = _operation_mode
            else:
                continue

        if operation_mode is None:
            self._logger.error("Couldn't find a valid operation mode.")
            return

        charging_capacity = operation_mode.elements[0].power_ranges[0].end_of_range

        start = self.now()
        start = start.replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)

        most_recent_system_description = next(
            reversed(self._system_description_history.values())
        )
        soc_at_start = (
            most_recent_system_description.storage.fill_level_range.end_of_range
            * FILL_LEVEL_SCALE
        )
        self._logger.debug(f"soc_at_start: {soc_at_start}")
        self._logger.debug(f"len(self._storage_status_history): {len(self._storage_status_history)}")

        if len(self._storage_status_history) > 0:
            last_storage_status: FRBCStorageStatus = next(
                reversed(self._storage_status_history.values())
            )
            self._logger.debug(f"last_storage_status: {last_storage_status}")
            self._logger.debug(f"last_storage_status.present_fill_level: {last_storage_status.present_fill_level}")
            soc_at_start = last_storage_status.present_fill_level * FILL_LEVEL_SCALE
            self._logger.debug(f"soc_at_start: {soc_at_start}")

        duration = timedelta(hours=24)

        flex_context = {
            "consumption-price": {"sensor": self._consumption_price_sensor_id},
            "production-price": {"sensor": self._production_price_sensor_id},
            "site-power-capacity": "1000MVA",
        }
        flex_model = {
                "state-of-charge": {"sensor": self._state_of_charge_sensor_id},
                "soc-at-start": f"{soc_at_start} {ENERGY_UNIT}",
                "soc-max": f"{soc_max} {ENERGY_UNIT}",
                "soc-min": f"{soc_min} {ENERGY_UNIT}",
                "soc-usage": [{"sensor": self._usage_forecast_sensor_id}],
                "storage-efficiency": {"sensor": self._leakage_behaviour_sensor_id},
                "charging-efficiency": {"sensor": self._thp_efficiency_sensor_id},
                "power-capacity": f"{charging_capacity} {POWER_UNIT}",
                "consumption-capacity": f"{charging_capacity} {POWER_UNIT}",
                "production-capacity": f"0 {POWER_UNIT}",
            }

        self._logger.debug("Triggering schedule with:")
        self._logger.debug(self._rm_discharge_sensor_id)
        self._logger.debug(start)
        self._logger.debug(duration)
        self._logger.debug(flex_context)
        self._logger.debug(flex_model)

        try:
            schedule = await self._fm_client.trigger_and_get_schedule(
                sensor_id=self._rm_discharge_sensor_id,
                start=start,
                duration=duration,
                flex_context=flex_context,
                flex_model=flex_model,
            )
        except HTTPError as exc:
            self._logger.error(f"Failed to get a schedule: {str(exc)}")
            return

        self._logger.debug("Schedule returned:")
        self._logger.debug(schedule)
        self._logger.debug("1")
        try:
            idx = pd.DatetimeIndex(
                pd.date_range(
                    start=start, end=start + duration - timedelta(minutes=15), freq="15min"
                )
            )
        except Exception as exc:
            self._logger.error(str(exc))
        self._logger.debug("2")
        try:
            self._logger.debug(f"Fetching THP efficiency (ID={self._thp_efficiency_sensor_id} from {start} for duration {duration}..")
            thp_efficiency = await self._fm_client.get_sensor_data(
                sensor_id=self._thp_efficiency_sensor_id,
                start=start,
                duration=duration,
                unit="dimensionless",
                resolution="PT15M",
            )
        except Exception as exc:
            self._logger.error(str(exc))
        self._logger.debug("3")
        try:
            thp_efficiency = pd.Series(
                thp_efficiency["values"], index=idx, name="thp_efficiency"
            )
        except Exception as exc:
            self._logger.error(str(exc))
        self._logger.debug("4")
        try:
            nes_efficiency = await self._fm_client.get_sensor_data(
                sensor_id=self._nes_efficiency_sensor_id,
                start=start,
                duration=duration,
                unit="dimensionless",
                resolution="PT15M",
            )
        except Exception as exc:
            self._logger.error(str(exc))
        self._logger.debug("5")
        nes_efficiency = pd.Series(
            nes_efficiency["values"], index=idx, name="nes_efficiency"
        )
        self._logger.debug("6")

        leakage_behaviour = await self._fm_client.get_sensor_data(
            sensor_id=self._leakage_behaviour_sensor_id,
            start=start,
            duration=duration,
            unit="dimensionless",
            resolution="PT15M",
        )
        self._logger.debug("7")

        leakage_behaviour = pd.Series(
            leakage_behaviour["values"], index=idx, name="leakage_behaviour"
        )
        self._logger.debug("8")

        try:
            usage_forecast = await self._fm_client.get_sensor_data(
                sensor_id=self._usage_forecast_sensor_id,
                start=start,
                duration=duration,
                unit=POWER_UNIT,
                resolution="PT15M",
            )
        except Exception as exc:
            self._logger.error(str(exc))
        self._logger.debug("9")

        usage_forecast = pd.Series(
            usage_forecast["values"], index=idx, name="usage_forecast"
        )
        self._logger.debug("10")

        schedule_series = pd.Series(schedule["values"], index=idx, name="schedule")
        self._logger.debug("11")

        try:
            schedule = pd.concat(
                [
                    thp_efficiency,
                    nes_efficiency,
                    schedule_series,
                    usage_forecast,
                    leakage_behaviour,
                ],
                axis=1,
            )
        except Exception as exc:
            self._logger.error(str(exc))
        self._logger.debug("12")

        instructions = fm_schedule_to_instructions(
            schedule, system_description, soc_at_start, logger=self._logger
        )
        self._logger.debug("Instructions generated:")
        self._logger.debug(instructions)

        # Put the instruction in the sending queue
        for instruction in instructions:
            await self._sending_queue.put(instruction)

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
        """
        Handle FRBC.SystemDescription messages.

        Process:
            1) Store system_description message for later.
            2) Send conversion efficiencies (COP) to FlexMeasures.
            3) Start a recurring tasks to trigger the scheduler.
        """

        message_dict = message.to_dict()
        message_dict.pop("message_id")
        system_description_hash = hash(str(message_dict))

        if self.last_system_description_hash == system_description_hash:
            return get_reception_status(message, status=ReceptionStatusValues.OK)
        else:
            self.last_system_description_hash = system_description_hash

        if not self.is_timer_due("handle_system_description"):
            return get_reception_status(message, status=ReceptionStatusValues.OK)

        system_description_id = str(message.message_id)

        # store system_description message for later
        self._system_description_history[system_description_id] = message

        # send conversion efficiencies
        task = asyncio.create_task(self.send_conversion_efficiencies(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)

        # schedule trigger_schedule to run soon concurrently
        task = asyncio.create_task(self.start_trigger_schedule())
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    async def send_conversion_efficiencies(
        self, system_description: FRBCSystemDescription
    ):
        """
        Send conversion efficiencies to FlexMeasures.

        Args:
            system_description (FRBCSystemDescription): The system description containing actuator details.
        """

        if not self.is_timer_due("send_conversion_efficiencies"):
            return

        start = system_description.valid_from
        actuator = system_description.actuators[0]

        # Calculate the number of samples based on the conversion efficiency duration
        N_SAMPLES = int(
            pd.Timedelta(CONVERSION_EFFICIENCY_DURATION) / pd.Timedelta(RESOLUTION)
        )

        N_SAMPLES = 1

        start_time = start.replace(
            minute=(start.minute // 15) * 15, second=0, microsecond=0
        )

        for operation_mode in actuator.operation_modes:
            if "THP" in operation_mode.diagnostic_label:
                sensor_id = self._thp_efficiency_sensor_id
            elif "NES" in operation_mode.diagnostic_label:
                sensor_id = self._nes_efficiency_sensor_id
            else:
                continue

            await self._fm_client.post_sensor_data(
                sensor_id=cast(int, sensor_id),
                start=start_time,
                values=[
                    3600
                    * operation_mode.elements[-1].fill_rate.end_of_range
                    * FILL_LEVEL_SCALE
                    / (operation_mode.elements[-1].power_ranges[0].end_of_range)
                ]
                * N_SAMPLES,
                unit="dimensionless",
                duration=CONVERSION_EFFICIENCY_DURATION,
            )

        # Send SOC Maxima and SOC Minima
        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_minima_sensor_id,
            start=start_time,
            values=system_description.storage.fill_level_range.start_of_range
            * FILL_LEVEL_SCALE,
            unit=ENERGY_UNIT,
            duration=CONVERSION_EFFICIENCY_DURATION,
        )

        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_maxima_sensor_id,
            start=start_time,
            values=system_description.storage.fill_level_range.end_of_range
            * FILL_LEVEL_SCALE,
            unit=ENERGY_UNIT,
            duration=CONVERSION_EFFICIENCY_DURATION,
        )

    async def close(self):
        """
        Closing procedure:
            1) Stop recurrent task
        """

        await self.stop_trigger_schedule()

    async def send_usage_forecast(self, usage_forecast: FRBCUsageForecast):
        """
        Send FRBC.UsageForecast to FlexMeasures.

        Args:
            usage_forecast (FRBCUsageForecast): The usage forecast to be translated and sent.
        """
        if not self.is_timer_due("usage_forecast"):
            return

        start_time = usage_forecast.start_time

        # flooring to previous 15min tick
        start_time = start_time.replace(
            minute=(start_time.minute // 15) * 15, second=0, microsecond=0
        )

        usage_forecast = translate_usage_forecast_to_fm(
            usage_forecast, RESOLUTION, strategy="mean"
        )

        scale = timedelta(minutes=15) / timedelta(seconds=1)

        await self._fm_client.post_sensor_data(
            sensor_id=self._usage_forecast_sensor_id,
            start=start_time,
            values=(usage_forecast * scale).tolist(),  # e.g. [0, 100] %/s ->  [0, 100] %/(15 min)
            unit=POWER_UNIT,  # e.g. [0, 100] MW/(15 min)
            duration=str(pd.Timedelta(RESOLUTION) * len(usage_forecast)),
        )

    async def send_fill_level_target_profile(
        self, fill_level_target_profile: FRBCFillLevelTargetProfile
    ):
        """
        Send FRBC.FillLevelTargetProfile to FlexMeasures.

        Args:
            fill_level_target_profile (FRBCFillLevelTargetProfile): The fill level target profile to be translated and sent.
        """
        if not self.is_timer_due("fill_level_target_profile"):
            return

        soc_minima, soc_maxima = translate_fill_level_target_profile(
            fill_level_target_profile,
            resolution=RESOLUTION,
        )

        duration = str(pd.Timedelta(RESOLUTION) * len(soc_maxima))

        # POST SOC Minima measurements to FlexMeasures
        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_minima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_minima,
            unit=POWER_UNIT,
            duration=duration,
        )

        # POST SOC Maxima measurements to FlexMeasures
        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_maxima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_maxima,
            unit=POWER_UNIT,
            duration=duration,
        )
