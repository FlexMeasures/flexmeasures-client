# flake8: noqa
"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

import asyncio
from datetime import datetime, timedelta

import pandas as pd

"""
Import optional dependency needed for timezone aware datetimes to avoid
a blocking call to import_module in an async loop (e.g. from HomeAssistant)
Error:  Detected blocking call to import_module with args ('tzdata',)
"""
from pandas.compat._optional import import_optional_dependency

import_optional_dependency("tzdata")


import pydantic
import pytz

try:
    from s2python.common import NumberRange, ReceptionStatus, ReceptionStatusValues
    from s2python.frbc import (
        FRBCActuatorStatus,
        FRBCFillLevelTargetProfile,
        FRBCInstruction,
        FRBCStorageStatus,
        FRBCSystemDescription,
        FRBCUsageForecast,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2 import register
from flexmeasures_client.s2.control_types.FRBC import FRBC
from flexmeasures_client.s2.control_types.translations import (
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)
from flexmeasures_client.s2.utils import get_reception_status, get_unique_id

RESOLUTION = "15min"
ENERGY_UNIT = "MWh"
POWER_UNIT = "MW"
DIMENSIONLESS = "dimensionless"
PERCENTAGE = "%"
TASK_PERIOD_SECONDS = 2
CONVERSION_EFFICIENCY_DURATION = "PT24H"


class FillRateBasedControlTUNES(FRBC):
    _fill_level_sensor_id: int | None

    _fill_rate_sensor_id: int | None
    _thp_fill_rate_sensor_id: int | None
    _thp_efficiency_sensor_id: int | None
    _nes_fill_rate_sensor_id: int | None
    _nes_efficiency_sensor_id: int | None

    _active_actuator_id_sensor_id: int | None

    _schedule_duration: timedelta

    _usage_forecast_sensor_id: int | None
    _soc_minima_sensor_id: int | None
    _soc_maxima_sensor_id: int | None

    _timers: dict[str, datetime]

    MIN_MEASUREMENT_PERIOD: int = 5  # in minutes

    def __init__(
        self,
        soc_minima_sensor_id: int | None = None,
        soc_maxima_sensor_id: int | None = None,
        fill_level_sensor_id: int | None = None,
        usage_forecast_sensor_id: int | None = None,
        thp_fill_rate_sensor_id: int | None = None,
        thp_efficiency_sensor_id: int | None = None,
        nes_fill_rate_sensor_id: int | None = None,
        nes_efficiency_sensor_id: int | None = None,
        fill_rate_sensor_id: int | None = None,
        rm_discharge_sensor_id: int | None = None,
        active_actuator_id_sensor_id: int | None = None,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
        **kwargs
    ) -> None:
        super().__init__(max_size)

        self._fill_level_sensor_id = fill_level_sensor_id

        self._fill_rate_sensor_id = fill_rate_sensor_id
        self._thp_fill_rate_sensor_id = thp_fill_rate_sensor_id
        self._thp_efficiency_sensor_id = thp_efficiency_sensor_id
        self._nes_fill_rate_sensor_id = nes_fill_rate_sensor_id
        self._nes_efficiency_sensor_id = nes_efficiency_sensor_id

        self._active_actuator_id_sensor_id = active_actuator_id_sensor_id

        self._schedule_duration = schedule_duration

        self._usage_forecast_sensor_id = usage_forecast_sensor_id
        self._soc_minima_sensor_id = soc_minima_sensor_id
        self._soc_maxima_sensor_id = soc_maxima_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id

        self._timezone = pytz.timezone(timezone)

        # delay the start of the schedule from the time `valid_from`
        # of the FRBC.SystemDescritption
        self._valid_from_shift = valid_from_shift

        self._active_recurring_schedule = False
        self._timers = dict()

    def is_timer_due(self, name: str):
        if (
            self._timers.get(
                name, datetime.now() - timedelta(minutes=self.MIN_MEASUREMENT_PERIOD)
            )
            < datetime.now()
        ):
            self._timers[name] = datetime.now() + timedelta(
                minutes=self.MIN_MEASUREMENT_PERIOD
            )
            return True
        else:
            return False

    def now(self):
        return self._timezone.localize(datetime.now())

    async def send_storage_status(self, status: FRBCStorageStatus):
        if not self.is_timer_due("storage_status"):
            return

        try:
            await self._fm_client.post_measurements(
                self._fill_level_sensor_id,
                start=self.now(),
                values=[status.present_fill_level],
                unit=ENERGY_UNIT,
                duration=timedelta(minutes=0),  # INSTANTANEOUS
            )
        except Exception as e:
            response = ReceptionStatus(
                subject_message_id=status.message_id,
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
        )

        # Send data to the sensor of the fill rate corresponding to the active operation mode
        await self._fm_client.post_measurements(
            sensor_id=active_operation_mode_fill_rate_sensor_id,
            start=dt,
            values=[fill_rate],
            unit=POWER_UNIT,
            duration=timedelta(minutes=15),
        )

        # Send data to the sensor of the input fill_rate to the storage device
        await self._fm_client.post_measurements(
            sensor_id=self._fill_rate_sensor_id,
            start=dt,
            values=[fill_rate],
            unit=POWER_UNIT,
            duration=timedelta(minutes=15),
        )

        await self._fm_client.post_measurements(
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
        soc_min = fill_level_range.end_of_range
        soc_max = fill_level_range.start_of_range

        operation_mode = actuator.operation_modes[0]
        operation_mode_factor = 0.1

        # TODO: 1) Call FlexMeasures
        # TODO: 2) Select with which actuator to send the instruction
        # TODO: 3) Create operation_mode_factor from power (we have a function for that)

        instruction = FRBCInstruction(
            message_id=get_unique_id(),
            id=get_unique_id(),
            actuator_id=actuator.id,
            operation_mode=operation_mode.id,  # Based on the expeted fill_level, select the best actuator (most efficient) to fulfill a certain fill_rate
            operation_mode_factor=operation_mode_factor,
            execution_time=self.now(),
            abnormal_condition=False,
        )

        # Put the instruction in the sending queue
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
            3) Start a recurring tasks to trigger the scehduler.
        """

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

        start = system_description.valid_from
        actuator = system_description.actuators[0]

        # Calculate the number of samples based on the conversion efficiency duration
        N_SAMPLES = int(
            pd.Timedelta(CONVERSION_EFFICIENCY_DURATION) / pd.Timedelta(RESOLUTION)
        )

        thp_op_mode_element = actuator.operation_modes[0].elements[-1]
        nes_op_mode_element = actuator.operation_modes[1].elements[-1]

        # THP efficiencies: Calculate and post measurements for THP efficiencies
        await self._fm_client.post_measurements(
            sensor_id=self._thp_efficiency_sensor_id,
            start=start,
            values=[
                100
                * thp_op_mode_element.fill_rate.end_of_range
                / thp_op_mode_element.power_ranges[0].end_of_range
            ]
            * N_SAMPLES,
            unit=PERCENTAGE,
            duration=CONVERSION_EFFICIENCY_DURATION,
        )

        # NES efficiencies: Calculate and post measurements for NES efficiencies
        await self._fm_client.post_measurements(
            sensor_id=self._nes_efficiency_sensor_id,
            start=start,
            values=[
                100
                * nes_op_mode_element.fill_rate.end_of_range
                / nes_op_mode_element.power_ranges[0].end_of_range
            ]
            * N_SAMPLES,
            unit=PERCENTAGE,
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
        # todo: floor to RESOLUTION

        usage_forecast = translate_usage_forecast_to_fm(
            usage_forecast, RESOLUTION, strategy="mean"
        )

        await self._fm_client.post_measurements(
            sensor_id=self._usage_forecast_sensor_id,
            start=start_time,
            values=usage_forecast.tolist(),
            unit=POWER_UNIT,
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
        await self._fm_client.post_measurements(
            sensor_id=self._soc_minima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_minima,
            unit=POWER_UNIT,
            duration=duration,
        )

        # POST SOC Maxima measurements to FlexMeasures
        await self._fm_client.post_measurements(
            sensor_id=self._soc_maxima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_maxima,
            unit=POWER_UNIT,
            duration=duration,
        )
