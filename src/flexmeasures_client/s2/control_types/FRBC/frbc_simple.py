"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

from datetime import datetime, timedelta

import pytz
from s2python.frbc import FRBCActuatorStatus, FRBCStorageStatus, FRBCSystemDescription

from flexmeasures_client.s2.control_types.FRBC import FRBC
from flexmeasures_client.s2.control_types.FRBC.utils import fm_schedule_to_instructions


class FRBCSimple(FRBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _soc_sensor_id: int
    _rm_discharge_sensor_id: int
    _schedule_duration: timedelta
    _valid_from_shift: timedelta

    def __init__(
        self,
        power_sensor_id: int,
        soc_sensor_id: int,
        rm_discharge_sensor_id: int,
        price_sensor_id: int,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
    ) -> None:
        super().__init__(max_size)
        self._power_sensor_id = power_sensor_id
        self._price_sensor_id = price_sensor_id
        self._schedule_duration = schedule_duration
        self._soc_sensor_id = soc_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id
        self._timezone = pytz.timezone(timezone)

        # delay the start of the schedule from the time `valid_from`
        # of the FRBC.SystemDescritption.
        self._valid_from_shift = valid_from_shift

    def now(self):
        return self._timezone.localize(datetime.now())

    async def send_storage_status(self, status: FRBCStorageStatus):
        await self._fm_client.post_measurements(
            self._soc_sensor_id,
            start=self.now(),
            values=[status.present_fill_level],
            unit="MWh",
            duration=timedelta(minutes=1),
        )

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        factor = status.operation_mode_factor
        sd: FRBCSystemDescription = list(self._system_description_history.values())[-1]
        fill_rate = sd.actuators[0].operation_modes[0].elements[0].fill_rate

        power = (
            fill_rate.start_of_range
            + (fill_rate.end_of_range - fill_rate.start_of_range) * factor
        )

        dt = status.transition_timestamp  # self.now()

        await self._fm_client.post_measurements(
            self._rm_discharge_sensor_id,
            start=dt,
            values=[-power],
            unit="MWh",
            duration=timedelta(minutes=15),
        )

        # await self._fm_client.post_measurements(
        #     self._soc_sensor_id
        # )

        # system_description = self.find_system_description_from_actuator()

        # if system_description is None:
        #     return

        # #for a
        # if system_description is not None:

        # self._system_description_history[]
        # status.active_operation_mode_id
        # status.actuator_id
        # status.operation_mode_factor

    async def trigger_schedule(self, system_description_id: str):
        """Translates S2 System Description into FM API calls"""

        system_description: FRBCSystemDescription = self._system_description_history[
            system_description_id
        ]

        if len(self._storage_status_history) > 0:
            soc_at_start = list(self._storage_status_history.values())[
                -1
            ].present_fill_level
        else:
            print("Can't trigger schedule without knowing the status of the storage...")
            return

        # call schedule
        schedule_id = await self._fm_client.trigger_storage_schedule(
            start=system_description.valid_from
            + self._valid_from_shift,  # TODO: localize datetime
            sensor_id=self._power_sensor_id,
            production_price_sensor=self._price_sensor_id,
            consumption_price_sensor=self._price_sensor_id,
            soc_unit="MWh",
            soc_at_start=soc_at_start,  # TODO: use forecast of the SOC instead
            duration=self._schedule_duration,  # next 12 hours
            # TODO: add SOC MAX AND SOC MIN FROM fill_level_range,
            # this needs chages on the client
        )

        # wait for the schedule to finish
        schedule = await self._fm_client.get_schedule(
            sensor_id=self._power_sensor_id,
            schedule_id=schedule_id,
            duration=self._schedule_duration,
        )

        # translate FlexMeasures schedule into instructions. SOC -> Power -> PowerFactor
        instructions = fm_schedule_to_instructions(
            schedule, system_description, soc_at_start
        )

        # put instructions to sending queue
        for instruction in instructions:
            await self._sending_queue.put(instruction)
