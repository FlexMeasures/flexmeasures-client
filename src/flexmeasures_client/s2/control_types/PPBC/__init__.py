import asyncio

import pydantic
from s2python.common import ControlType, ReceptionStatusValues
from s2python.ppbc import PPBCPowerProfileDefinition, PPBCPowerProfileStatus

from flexmeasures_client.s2 import SizeLimitOrderedDict, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.utils import get_reception_status

# from flexmeasures_client.s2.utils import get_reception_status, get_unique_id


class PPBC(ControlTypeHandler):
    _control_type = ControlType.POWER_PROFILE_BASED_CONTROL

    _power_profile_definition_history: SizeLimitOrderedDict[
        str, PPBCPowerProfileDefinition
    ]
    _power_profile_status_history: SizeLimitOrderedDict[str, PPBCPowerProfileStatus]

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._power_profile_definition_history = SizeLimitOrderedDict(max_size=max_size)
        self._power_profile_status_history = SizeLimitOrderedDict(max_size=max_size)
        self.background_tasks = set()

    @register(PPBCPowerProfileDefinition)
    def handle_power_profile_definition(
        self, message: PPBCPowerProfileDefinition
    ) -> pydantic.BaseModel:
        power_profile_id = str(message.message_id)

        # Store the power profile definition
        self._power_profile_definition_history[power_profile_id] = message

        task = asyncio.create_task(self.send_power_profile_definition(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(PPBCPowerProfileStatus)
    def handle_power_profile_status(
        self, message: PPBCPowerProfileStatus
    ) -> pydantic.BaseModel:
        power_profile_status_message_id = str(message.message_id)

        # Store the power profile status
        self._power_profile_status_history[power_profile_status_message_id] = message

        task = asyncio.create_task(self.send_power_profile_status(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    async def send_power_profile_definition(self, message: PPBCPowerProfileDefinition):
        raise NotImplementedError()

    async def send_power_profile_status(self, message: PPBCPowerProfileStatus):
        raise NotImplementedError()
