import asyncio

import pydantic

try:
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
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
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
    background_tasks: set
    _last_system_description_fingerprint: int | None

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
        self.background_tasks = set()

        self._last_system_description_fingerprint = None

    @staticmethod
    def system_description_fingerprint(message: FRBCSystemDescription) -> int:
        """Hash what a system description says, ignoring which message said it.

        A resource manager commonly re-sends its system description with every request,
        under a fresh message id each time, because the specification offers no other way to say
        "unchanged".
        The fingerprint is therefore what distinguishes a genuine change from a resend.
        """
        content = message.to_dict()
        content.pop("message_id", None)
        return hash(str(content))

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
        # A re-sent description is acknowledged, but not stored and not acted on.
        # Acting on it would trigger a schedule and a full instruction batch per resend,
        # which is redundant work on the server and redundant traffic on the serial S2 link,
        # and it was a large part of the instruction storms seen in the FLEXED co-simulation.
        # The TUNES handler has fingerprinted its descriptions for the same reason all along;
        # this brings the base handler in line.
        fingerprint = self.system_description_fingerprint(message)
        if fingerprint == self._last_system_description_fingerprint:
            self._logger.debug(
                "Ignoring a re-sent FRBC.SystemDescription: its content is unchanged."
            )
            return get_reception_status(message, status=ReceptionStatusValues.OK)
        self._last_system_description_fingerprint = fingerprint

        system_description_id = str(message.message_id)

        # store system_description message for later
        self._system_description_history[system_description_id] = message

        # schedule trigger_schedule to run soon concurrently
        task = asyncio.create_task(self.trigger_schedule(system_description_id))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCUsageForecast)
    def handle_usage_forecast(self, message: FRBCUsageForecast) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._usage_forecast_history[message_id] = message

        task = asyncio.create_task(self.send_usage_forecast(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCStorageStatus)
    def handle_storage_status(self, message: FRBCStorageStatus) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._storage_status_history[message_id] = message

        task = asyncio.create_task(self.send_storage_status(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCActuatorStatus)
    def handle_actuator_status(self, message: FRBCActuatorStatus) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._actuator_status_history[message_id] = message

        task = asyncio.create_task(self.send_actuator_status(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCLeakageBehaviour)
    def handle_leakage_behaviour(
        self, message: FRBCLeakageBehaviour
    ) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._leakage_behaviour_history[message_id] = message

        task = asyncio.create_task(self.send_leakage_behaviour(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCFillLevelTargetProfile)
    def handle_fill_level_target_profile(
        self, message: FRBCFillLevelTargetProfile
    ) -> pydantic.BaseModel:
        message_id = str(message.message_id)

        self._fill_level_target_profile_history[message_id] = message

        task = asyncio.create_task(self.send_fill_level_target_profile(message))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCTimerStatus)
    def handle_frbc_timer_status(self, message: FRBCTimerStatus) -> pydantic.BaseModel:
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    async def send_storage_status(self, status: FRBCStorageStatus):
        raise NotImplementedError()

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        raise NotImplementedError()

    async def send_leakage_behaviour(self, leakage_behaviour: FRBCLeakageBehaviour):
        raise NotImplementedError()

    async def send_usage_forecast(self, usage_forecast: FRBCUsageForecast):
        raise NotImplementedError()

    async def send_fill_level_target_profile(
        self, fill_level_target_profile: FRBCFillLevelTargetProfile
    ):
        raise NotImplementedError()


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

        await self.send_message(instruction)
