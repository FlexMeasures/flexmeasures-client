import asyncio

import pydantic
from python_s2_protocol.common.messages import ReceptionStatusValues
from python_s2_protocol.common.schemas import ControlType
from python_s2_protocol.FRBC.messages import (
    FRBCActuatorStatus,
    FRBCFillLevelTargetProfile,
    FRBCInstruction,
    FRBCLeakageBehaviour,
    FRBCStorageStatus,
    FRBCSystemDescription,
    FRBCTimerStatus,
    FRBCUsageForecast,
)

from flexmeasures_client.s2 import SizeLimitOrderedDict, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.utils import get_reception_status, get_unique_id


class FRBC(ControlTypeHandler):
    _control_type = ControlType.FILL_RATE_BASED_CONTROL

    _system_description_historic: SizeLimitOrderedDict[str, FRBCSystemDescription]

    _fill_level_target_profile_historic: SizeLimitOrderedDict[
        str, FRBCFillLevelTargetProfile
    ]
    _leakage_behaviour_historic: SizeLimitOrderedDict[str, FRBCLeakageBehaviour]
    _usage_forecast_historic: SizeLimitOrderedDict[str, FRBCUsageForecast]

    _timer_status_historic: SizeLimitOrderedDict[str, FRBCTimerStatus]
    _actuator_status_historic: SizeLimitOrderedDict[str, FRBCActuatorStatus]
    _storage_status_historic: SizeLimitOrderedDict[str, FRBCStorageStatus]

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._system_description_historic = SizeLimitOrderedDict(max_size=max_size)

        self._fill_level_target_profile_historic = SizeLimitOrderedDict(
            max_size=max_size
        )
        self._leakage_behaviour_historic = SizeLimitOrderedDict(max_size=max_size)
        self._usage_forecast_historic = SizeLimitOrderedDict(max_size=max_size)

        self._timer_status_historic = SizeLimitOrderedDict(max_size=max_size)
        self._actuator_status_historic = SizeLimitOrderedDict(max_size=max_size)
        self._storage_status_historic = SizeLimitOrderedDict(max_size=max_size)

        self._system_description_historic = SizeLimitOrderedDict(max_size=max_size)
        self._leakage_behaviour_historic = SizeLimitOrderedDict(max_size=max_size)
        self._usage_forecast_historic = SizeLimitOrderedDict(max_size=max_size)

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
        system_description_id = message.message_id.__root__

        # store system_description message for later
        self._system_description_historic[system_description_id] = message

        # schedule trigger_schedule to run soon concurrently
        asyncio.create_task(self.trigger_schedule(system_description_id))

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCLeakageBehaviour)
    def handle_leakage_behaviour(
        self, message: FRBCLeakageBehaviour
    ) -> pydantic.BaseModel:
        # return get_reception_status(message, status=ReceptionStatusValues.OK)
        raise NotImplementedError()

    @register(FRBCUsageForecast)
    def handle_usage_forecast(self, message: FRBCUsageForecast) -> pydantic.BaseModel:
        # return get_reception_status(message, status=ReceptionStatusValues.OK)
        raise NotImplementedError()

    async def trigger_schedule(self, system_description_id: str):
        raise NotImplementedError()


class FRBCTest(FRBC):
    async def trigger_schedule(self, system_description_id: str):
        """Translates S2 System Description into FM API calls"""

        system_description: FRBCSystemDescription = self._system_description_historic[
            system_description_id
        ]

        instruction = FRBCInstruction(
            message_id=get_unique_id(),
            id=get_unique_id(),
            actuator_id=system_description.actuators[0].id.__root__,
            operation_mode=system_description.actuators[0]
            .operation_modes[0]
            .id.__root__,
            operation_mode_factor=0.5,
            execution_time=system_description.valid_from,
            abnormal_condition=False,
        )

        await self._sending_queue.put(instruction)