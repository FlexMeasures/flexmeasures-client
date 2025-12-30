# flake8: noqa
"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

import asyncio
import json
import math
from datetime import datetime, timedelta

import pandas as pd
from requests.exceptions import HTTPError

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
    from s2python.common import (
        NumberRange,
        ReceptionStatus,
        ReceptionStatusValues,
        RevokableObjects,
        RevokeObject,
    )
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
    leakage_behaviour_to_storage_efficiency,
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)
from flexmeasures_client.s2.utils import get_reception_status, get_unique_id

RESOLUTION = "15min"
ENERGY_UNIT = "MWh"
POWER_UNIT = "MW"
DIMENSIONLESS = "dimensionless"
PERCENTAGE = "%"
TASK_PERIOD_SECONDS = 15 * 60
CONVERSION_EFFICIENCY_DURATION = f"PT99H"


class FillRateBasedControlTUNES(FRBC):
    _asset_id: int
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

    _datastore: dict
    _timers: dict[str, datetime]
    _minimum_measurement_period: timedelta = timedelta(minutes=5)
    _safety_margin = 60  # in ENERGY_UNIT

    def __init__(
        self,
        asset_id: int,
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
        leakage_behaviour_sensor_id: int,
        production_price_sensor: int,
        consumption_price_sensor: int,
        state_of_charge_sensor_id: int,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
        timers: dict[str, datetime] | None = None,
        datastore: dict | None = None,
        **kwargs,
    ) -> None:
        super().__init__(max_size)

        self._asset_id = asset_id
        self._fill_level_sensor_id = fill_level_sensor_id

        self._fill_rate_sensor_id = fill_rate_sensor_id
        self._thp_fill_rate_sensor_id = thp_fill_rate_sensor_id
        self._thp_efficiency_sensor_id = thp_efficiency_sensor_id
        self._nes_fill_rate_sensor_id = nes_fill_rate_sensor_id
        self._nes_efficiency_sensor_id = nes_efficiency_sensor_id
        self._leakage_behaviour_sensor_id = leakage_behaviour_sensor_id

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
        self._datastore = datastore if datastore is not None else {}

    def _is_timer_due(self, name: str) -> bool:
        now = datetime.now()
        due_time = self._timers.get(name, now - self._minimum_measurement_period)
        if due_time <= now:
            # Get total seconds of the period
            period_seconds = self._minimum_measurement_period.total_seconds()

            # Seconds since start of the hour
            seconds_since_hour = now.minute * 60 + now.second + now.microsecond / 1e6

            # Ceil to next multiple of period_seconds
            next_tick_seconds = (
                math.ceil(seconds_since_hour / period_seconds) * period_seconds
            )

            # Compute next due datetime
            next_due = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                seconds=next_tick_seconds
            )
            self._timers[name] = next_due
            return True
        else:
            self._logger.debug(
                f"Timer for {name} is not due until {self._timers[name]}"
            )
            return False

    def now(self):
        return datetime.now(self._timezone)

    async def send_storage_status(self, status: FRBCStorageStatus):
        if not self._is_timer_due("storage_status"):
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
        await self.trigger_schedule()

    async def send_leakage_behaviour(self, leakage: FRBCLeakageBehaviour):
        if not self._is_timer_due("leakage_behaviour"):
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
                    leakage_behaviour_to_storage_efficiency(
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
        if not self._is_timer_due("actuator_status"):
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

    async def trigger_schedule(self):
        """
        Ask FlexMeasures for a new schedule and create FRBC.Instructions to send back to the ResourceManager
        """
        if not self._is_timer_due("trigger_schedule"):
            return

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
        efficiency_sensor_id = None
        for _operation_mode in actuator.operation_modes:
            if "THP" in _operation_mode.diagnostic_label:
                operation_mode = _operation_mode
                efficiency_sensor_id = self._thp_efficiency_sensor_id
            elif "NES" in _operation_mode.diagnostic_label:
                operation_mode = _operation_mode
                efficiency_sensor_id = self._nes_efficiency_sensor_id
            else:
                continue

        if operation_mode is None:
            self._logger.error("Couldn't find a valid operation mode.")
            return

        charging_capacity = operation_mode.elements[0].power_ranges[0].end_of_range

        start = self.now()
        start = start.replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)

        # Find soc-at-start
        if len(self._storage_status_history) > 0:
            last_storage_status: FRBCStorageStatus = next(
                reversed(self._storage_status_history.values())
            )
            soc_at_start = last_storage_status.present_fill_level * FILL_LEVEL_SCALE
        else:
            self._logger.info(f"No present fill level known: assuming an empty buffer.")
            most_recent_system_description = next(
                reversed(self._system_description_history.values())
            )
            soc_at_start = (
                most_recent_system_description.storage.fill_level_range.start_of_range
                * FILL_LEVEL_SCALE
            )

        planning_duration = timedelta(hours=24)
        schedule_duration = timedelta(hours=6)

        flex_context = {
            "consumption-price": {"sensor": self._consumption_price_sensor_id},
            "production-price": {"sensor": self._production_price_sensor_id},
            "site-power-capacity": f"{2 * 25 * 230} MVA",
            "relax-constraints": True,
        }
        flex_model = {
            "state-of-charge": {"sensor": self._state_of_charge_sensor_id},
            "soc-at-start": f"{soc_at_start} {ENERGY_UNIT}",
            "soc-max": f"{soc_max} {ENERGY_UNIT}",
            "soc-min": f"{soc_min} {ENERGY_UNIT}",
            "soc-minima": f"{max(soc_min, self._safety_margin)} {ENERGY_UNIT}",
            "soc-usage": [{"sensor": self._usage_forecast_sensor_id}],
            "storage-efficiency": {"sensor": self._leakage_behaviour_sensor_id},
            "charging-efficiency": {"sensor": efficiency_sensor_id},
            "power-capacity": f"{charging_capacity} {POWER_UNIT}",
            "consumption-capacity": f"{charging_capacity} {POWER_UNIT}",
            "production-capacity": f"0 {POWER_UNIT}",
        }

        self._logger.debug("Triggering schedule with:")
        self._logger.debug(self._rm_discharge_sensor_id)
        self._logger.debug(start)
        self._logger.debug(f"planning_duration: {planning_duration}")
        self._logger.debug(f"schedule_duration: {schedule_duration}")
        self._logger.debug(flex_context)
        self._logger.debug(flex_model)

        try:
            schedule_id = await self._fm_client.trigger_schedule(
                sensor_id=self._rm_discharge_sensor_id,
                start=start,
                duration=planning_duration,
                flex_context=flex_context,
                flex_model=flex_model,
            )
            schedule = await self._fm_client.get_schedule(
                sensor_id=self._rm_discharge_sensor_id,
                schedule_id=schedule_id,
                duration=schedule_duration,
            )
        except HTTPError as exc:
            self._logger.error(f"Failed to get a schedule: {str(exc)}")
            return

        self._logger.debug("Schedule returned:")
        self._logger.debug(schedule)
        try:
            idx = pd.DatetimeIndex(
                pd.date_range(
                    start=start,
                    end=start + schedule_duration - timedelta(minutes=15),
                    freq="15min",
                )
            )
        except Exception as exc:
            self._logger.error(str(exc))
        try:
            thp_efficiency = await self._fm_client.get_sensor_data(
                sensor_id=self._thp_efficiency_sensor_id,
                start=start,
                duration=schedule_duration,
                unit="dimensionless",
                resolution="PT15M",
            )
        except Exception as exc:
            self._logger.error(str(exc))
        try:
            thp_efficiency = pd.Series(
                thp_efficiency["values"], index=idx, name="thp_efficiency"
            )
        except Exception as exc:
            self._logger.error(str(exc))
        try:
            nes_efficiency = await self._fm_client.get_sensor_data(
                sensor_id=self._nes_efficiency_sensor_id,
                start=start,
                duration=schedule_duration,
                unit="dimensionless",
                resolution="PT15M",
            )
        except Exception as exc:
            self._logger.error(str(exc))
        nes_efficiency = pd.Series(
            nes_efficiency["values"], index=idx, name="nes_efficiency"
        )

        leakage_behaviour = await self._fm_client.get_sensor_data(
            sensor_id=self._leakage_behaviour_sensor_id,
            start=start,
            duration=schedule_duration,
            unit="dimensionless",
            resolution="PT15M",
        )

        leakage_behaviour = pd.Series(
            leakage_behaviour["values"], index=idx, name="leakage_behaviour"
        )

        try:
            usage_forecast = await self._fm_client.get_sensor_data(
                sensor_id=self._usage_forecast_sensor_id,
                start=start,
                duration=schedule_duration,
                unit=POWER_UNIT,
                resolution="PT15M",
            )
        except Exception as exc:
            self._logger.error(str(exc))

        usage_forecast = pd.Series(
            usage_forecast["values"], index=idx, name="usage_forecast"
        )

        schedule_series = pd.Series(schedule["values"], index=idx, name="schedule")

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

        instructions = fm_schedule_to_instructions(
            schedule, system_description, soc_at_start, logger=self._logger
        )

        # Revoke all previous instructions
        if (n_previous_instruction := len(self._datastore.get("instructions", {}))) > 0:
            self._logger.debug(
                f"Revoking all {n_previous_instruction} previous instructions.."
            )
        else:
            self._logger.debug("No previous instructions to revoke..")
        for message_id, instruction in self._datastore.get("instructions", {}).items():
            revoke_instruction = RevokeObject(
                message_id=get_unique_id(),
                object_type=RevokableObjects.FRBC_Instruction,
                object_id=message_id,
            )
            self._logger.debug(f"Sending revoke instruction for {message_id}")
            await self._sending_queue.put(revoke_instruction)
        self._datastore["instructions"] = {}

        # Put the instruction in the sending queue
        for instruction in instructions:
            await self._sending_queue.put(instruction)

        # Store instructions
        for instruction in instructions:
            self._datastore["instructions"][
                instruction.message_id
            ] = instruction.to_json()

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
        """
        Handle FRBC.SystemDescription messages.

        Process:
            1) Store system_description message for later.
            2) Send conversion efficiencies (COP) to FlexMeasures.
        """

        message_dict = message.to_dict()
        message_dict.pop("message_id")
        system_description_hash = hash(str(message_dict))

        if self.last_system_description_hash == system_description_hash:
            return get_reception_status(message, status=ReceptionStatusValues.OK)
        else:
            self.last_system_description_hash = system_description_hash

        if not self._is_timer_due("handle_system_description"):
            return get_reception_status(message, status=ReceptionStatusValues.OK)

        system_description_id = str(message.message_id)

        # store system_description message for later
        self._system_description_history[system_description_id] = message

        # update the asset's flex-model
        task = asyncio.create_task(self.update_flex_model(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)

        # send conversion efficiencies
        task = asyncio.create_task(self.send_conversion_efficiencies(message))
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

        if not self._is_timer_due("send_conversion_efficiencies"):
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

    async def update_flex_model(self, system_description: FRBCSystemDescription):
        """
        Update the asset's attributes and flex-model in FlexMeasures.

        Args:
            system_description (FRBCSystemDescription): The system description containing actuator details.
        """

        if not self._is_timer_due("update_flex_model"):
            return

        soc_min = f"{system_description.storage.fill_level_range.start_of_range * FILL_LEVEL_SCALE} {ENERGY_UNIT}"
        soc_max = f"{system_description.storage.fill_level_range.end_of_range * FILL_LEVEL_SCALE} {ENERGY_UNIT}"

        await self._fm_client.update_asset(
            asset_id=self._asset_id,
            updates=dict(
                attributes=json.loads(system_description.to_json()),
                flex_model={
                    "soc-min": soc_min,
                    "soc-max": soc_max,
                    "prefer-charging-sooner": True,
                    "prefer-curtailing-later": True,
                },
            ),
        )

    async def close(self):
        """
        Can be used to stop recurring tasks.
        """
        self._logger.debug(f"Closing {self.__class__.__name__} handler")

    async def send_usage_forecast(self, usage_forecast: FRBCUsageForecast):
        """
        Send FRBC.UsageForecast to FlexMeasures.

        Args:
            usage_forecast (FRBCUsageForecast): The usage forecast to be translated and sent.
        """
        if not self._is_timer_due("usage_forecast"):
            return

        start_time = usage_forecast.start_time

        # flooring to previous 15min tick
        start_time = start_time.replace(
            minute=(start_time.minute // 15) * 15, second=0, microsecond=0
        )

        usage_forecast = translate_usage_forecast_to_fm(
            usage_forecast, RESOLUTION, strategy="mean"
        )

        # Scale usage forecast e.g. [0, 100] %/s ->  [0, 100] %/(15 min)
        scale = timedelta(minutes=15) / timedelta(seconds=1)
        scaled_usage_forecast = usage_forecast * scale

        await self._fm_client.post_sensor_data(
            sensor_id=self._usage_forecast_sensor_id,
            start=start_time,
            values=scaled_usage_forecast.tolist(),
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
        if not self._is_timer_due("fill_level_target_profile"):
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
            values=soc_minima.tolist(),
            unit=ENERGY_UNIT,
            duration=duration,
        )

        # POST SOC Maxima measurements to FlexMeasures
        await self._fm_client.post_sensor_data(
            sensor_id=self._soc_maxima_sensor_id,
            start=fill_level_target_profile.start_time,
            values=soc_maxima.tolist(),
            unit=ENERGY_UNIT,
            duration=duration,
        )
