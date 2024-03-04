import asyncio

import pydantic
from s2python.common import ControlType, ReceptionStatusValues
from s2python.frbc import (
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

    _system_description_history: SizeLimitOrderedDict[str, FRBCSystemDescription]

    _fill_level_target_profile_history: SizeLimitOrderedDict[
        str, FRBCFillLevelTargetProfile
    ]
    _leakage_behaviour_history: SizeLimitOrderedDict[str, FRBCLeakageBehaviour]
    _usage_forecast_history: SizeLimitOrderedDict[str, FRBCUsageForecast]

    _timer_status_history: SizeLimitOrderedDict[str, FRBCTimerStatus]
    _actuator_status_history: SizeLimitOrderedDict[str, FRBCActuatorStatus]
    _storage_status_history: SizeLimitOrderedDict[str, FRBCStorageStatus]

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._system_description_history = SizeLimitOrderedDict(max_size=max_size)

        self._fill_level_target_profile_history = SizeLimitOrderedDict(
            max_size=max_size
        )
        self._leakage_behaviour_history = SizeLimitOrderedDict(max_size=max_size)
        self._usage_forecast_history = SizeLimitOrderedDict(max_size=max_size)

        self._timer_status_history = SizeLimitOrderedDict(max_size=max_size)
        self._actuator_status_history = SizeLimitOrderedDict(max_size=max_size)
        self._storage_status_history = SizeLimitOrderedDict(max_size=max_size)

        self._system_description_history = SizeLimitOrderedDict(max_size=max_size)
        self._leakage_behaviour_history = SizeLimitOrderedDict(max_size=max_size)
        self._usage_forecast_history = SizeLimitOrderedDict(max_size=max_size)

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
        system_description_id = str(message.message_id)

        # store system_description message for later
        self._system_description_history[system_description_id] = message

        # schedule trigger_schedule to run soon concurrently
        asyncio.create_task(self.trigger_schedule(system_description_id))

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    async def send_storage_status(self, status: FRBCStorageStatus):
        raise NotImplementedError()

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        raise NotImplementedError()

    @register(FRBCStorageStatus)
    def handle_storage_status(self, message: FRBCStorageStatus) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._storage_status_history[message_id] = message

        asyncio.create_task(self.send_storage_status(message))

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCActuatorStatus)
    def handle_actuator_status(self, message: FRBCActuatorStatus) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._actuator_status_history[message_id] = message

        asyncio.create_task(self.send_actuator_status(message))

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

    @register(FRBCTimerStatus)
    def handle_frbc_timer_status(self, message: FRBCTimerStatus) -> pydantic.BaseModel:
        return get_reception_status(message, status=ReceptionStatusValues.OK)


class FRBCTest(FRBC):
    """Dummy class to simulate the triggering of a schedule."""

    async def trigger_schedule(self, system_description_id: str):
        """Creates schedule consisting on just a dummy instruction

        :param system_description_id: system description to based the schedule on
        """

        system_description: FRBCSystemDescription = self._system_description_history[
            system_description_id
        ]

        actuator = system_description.actuators[0]

        instruction = FRBCInstruction(
            message_id=get_unique_id(),
            id=get_unique_id(),
            actuator_id=actuator.id,
            operation_mode=actuator.operation_modes[0].id,
            operation_mode_factor=0.5,
            execution_time=system_description.valid_from,
            abnormal_condition=False,
        )

        # put instruction into the sending queue
        await self._sending_queue.put(instruction)
