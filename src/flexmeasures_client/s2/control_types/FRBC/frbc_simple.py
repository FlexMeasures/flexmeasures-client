from datetime import timedelta

from flexmeasures_client.s2.control_types.FRBC import FRBC
from flexmeasures_client.s2.control_types.FRBC.utils import fm_schedule_to_instructions
from flexmeasures_client.s2.python_s2_protocol.FRBC.messages import (
    FRBCSystemDescription,
)


class FRBCSimple(FRBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _schedule_duration: timedelta

    def __init__(
        self,
        power_sensor_id: int,
        price_sensor_id: int,
        schedule_duration: timedelta = timedelta(hours=12),
        max_size: int = 100,
    ) -> None:
        super().__init__(max_size)
        self._power_sensor_id = power_sensor_id
        self._price_sensor_id = price_sensor_id
        self._schedule_duration = schedule_duration

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
            start=system_description.valid_from,  # TODO: localize datetime
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
