"""
This module contains the PPBC simple control type.
"""

from datetime import datetime, timedelta

import pytz
from s2python.ppbc import PPBCScheduleInstruction

from flexmeasures_client.s2.control_types.PPBC import PPBC


class PPBCSimple(PPBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _schedule_duration: timedelta
    _valid_from_shift: timedelta

    def __init__(
        self,
        power_sensor_id: int,
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
        self._timezone = pytz.timezone(timezone)

        # delay the start of the schedule from the time `valid_from`
        # of the PPBC.SystemDescription.
        self._valid_from_shift = valid_from_shift

    def now(self):
        return self._timezone.localize(datetime.now())

    # todo: let's make this more like FRBCSimple.trigger_schedule:
    #       a) call self._fm_client.trigger_and_get_schedule
    #       b) put instructions to sending queue
    async def send_schedule_instruction(self, instruction: PPBCScheduleInstruction):
        await self._fm_client.post_schedule(
            self._power_sensor_id,
            start=self.now(),
            values=instruction.power_values,
            unit="MW",
            duration=self._schedule_duration,
            price_sensor_id=self._price_sensor_id,
            price_values=instruction.price_values,
            price_unit="EUR/MWh",
            valid_from=self.now() + self._valid_from_shift,
        )

    async def trigger_schedule(self, definition_id: str):
        definition: PPBCScheduleInstruction = self._power_profile_definition_history[
            definition_id
        ]

        if len(self._power_profile_status_history) == 0:
            print("Can't trigger schedule without knowing the status of the profile...")
            return

        # Call schedule
        # schedule = await self._fm_client.trigger_and_get_schedule(
        #     start=definition.start_time + self._valid_from_shift,
        #     sensor_id=self._power_sensor_id,
        #     flex_context=dict(
        #         production_price_sensor=self._price_sensor_id,
        #         consumption_price_sensor=self._price_sensor_id,
        #     ),
            
        # )
