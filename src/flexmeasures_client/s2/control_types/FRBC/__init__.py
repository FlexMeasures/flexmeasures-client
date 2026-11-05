import asyncio

import pydantic

try:
    from s2python.common import (
        ControlType,
        InstructionStatus,
        ReceptionStatusValues,
        RevokableObjects,
        RevokeObject,
    )
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

# An instruction the resource manager has not started yet can still be called off.
# One it has started, finished or already refused cannot, and a revocation for it would only be
# noise on the link, or an error from a strict resource manager.
PENDING_INSTRUCTION_STATUSES = (InstructionStatus.NEW, InstructionStatus.ACCEPTED)

# The kind of request that storage statuses are, for the purpose of superseding:
# each one reports a new state of charge and so asks for a schedule from that state,
# and only the newest such request is worth answering.
STORAGE_STATUS_REQUESTS = "storage_status"


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
    _sent_instructions: dict[str, FRBCInstruction]
    send_revocations: bool
    _request_counts: dict[str, int]
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

        # The instructions of the batch the resource manager is currently working from, by their
        # instruction id, which is also the id it reports status updates against.
        # A new batch replaces them, because an FRBC schedule is sent whole rather than amended.
        self._sent_instructions = {}
        # Whether replacing a batch also revokes the instructions of the batch before it.
        # Off by default: a resource manager is not required to implement RevokeObject,
        # and one that keys instructions to its current request discards the old batch by itself
        # (the FLEXED co-simulation resource manager does exactly that).
        # A deployment against a resource manager that honours revocations should turn it on.
        self.send_revocations = False
        # One counter per kind of request, counting the requests of that kind received so far.
        # A task spawned for a request notes the count at the time, and stands down if a newer
        # request of the same kind has arrived by the time it runs, or by the time it has an
        # answer to act on (see _is_latest_request).
        self._request_counts = {}
        self._last_system_description_fingerprint = None

    def _count_request(self, kind: str) -> int:
        """Count a newly received request of the given kind, and return its number."""
        number = self._request_counts.get(kind, 0) + 1
        self._request_counts[kind] = number
        return number

    def _is_latest_request(self, kind: str, number: int) -> bool:
        """Whether that numbered request is still the latest of its kind."""
        return self._request_counts.get(kind, 0) == number

    @staticmethod
    def filter_instruction_transitions(
        instructions: list[FRBCInstruction],
    ) -> list[FRBCInstruction]:
        """Keep only the instructions that change what an actuator is doing.

        An FRBC instruction is a switch command, not a setpoint sample: the resource manager holds
        the operation mode and factor it was last given until it is told otherwise.
        An instruction repeating its predecessor's actuator, operation mode and factor therefore
        asks for nothing, and a schedule expressed as one instruction per quarter hour over a day
        is mostly made of such repeats.
        Instructions for different actuators are independent, so each actuator is followed
        separately, and the first instruction for an actuator is always kept, since it is what
        establishes that actuator's state.
        """
        filtered: list[FRBCInstruction] = []
        last_state: dict[str, tuple[str, float]] = {}
        for instruction in instructions:
            actuator = str(instruction.actuator_id)
            state = (
                str(instruction.operation_mode),
                float(instruction.operation_mode_factor),
            )
            if last_state.get(actuator) == state:
                continue
            last_state[actuator] = state
            filtered.append(instruction)
        return filtered

    async def send_instruction_batch(
        self,
        instructions: list[FRBCInstruction],
        request_kind: str | None = None,
        request_number: int | None = None,
    ) -> int:
        """Replace the outstanding instruction batch with a new one, and return what was sent.

        The transition filter is applied first.
        A batch computed for a request that has since been superseded is then dropped rather than
        sent: the resource manager would file it under its current request, which is not the
        request this batch answers (see _is_latest_request).
        What remains is sent, after the previous batch has been revoked where this handler is
        configured to revoke, and is recorded as the outstanding batch.
        """
        filtered = self.filter_instruction_transitions(instructions)

        if (
            request_kind is not None
            and request_number is not None
            and not self._is_latest_request(request_kind, request_number)
        ):
            self._logger.info(
                f"Dropping an instruction batch of {len(filtered)} instructions: "
                f"a newer '{request_kind}' request arrived while it was being computed."
            )
            return 0

        if self.send_revocations:
            for instruction_id, instruction in self._sent_instructions.items():
                status = self._instruction_status_history.get(
                    instruction_id, InstructionStatus.NEW
                )
                if status not in PENDING_INSTRUCTION_STATUSES:
                    continue
                await self.send_message(
                    RevokeObject(
                        message_id=get_unique_id(),
                        object_type=RevokableObjects.FRBC_Instruction,
                        object_id=instruction.id,
                    )
                )

        self._sent_instructions = {}
        for instruction in filtered:
            await self.send_message(instruction)
            self._sent_instructions[str(instruction.id)] = instruction

        self._logger.debug(
            f"Sent an instruction batch of {len(filtered)} instructions, "
            f"out of {len(instructions)} before transition-filtering."
        )
        return len(filtered)

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
        # A resend is acknowledged, and otherwise ignored: storing it again would push a
        # description out of a bounded history for no reason, and a handler that did act on a
        # description would act on it twice.
        # Acknowledging it still matters, or the resource manager retries.
        # Ignoring a resend is safe now that planning hangs off storage statuses rather than
        # descriptions, and not before: it would otherwise have dropped a planning request.
        # The TUNES handler has fingerprinted its descriptions this way all along, and can adopt
        # this shared helper separately.
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

        # A description says what the device can do, and a storage status what it holds now,
        # and a schedule needs both. Planning normally hangs off a storage status (see handle_storage_status),
        # but a description that arrives after the latest status, or that changes what the device can do,
        # is answered with a schedule against that latest status.
        # Otherwise a resource manager that sends its status first, or whose fill level holds steady
        # so that no new status follows, is never planned for, or keeps being planned for with what it can no longer do.
        if self._storage_status_history:
            request_number = self._count_request(STORAGE_STATUS_REQUESTS)
            task = asyncio.create_task(
                self.trigger_schedule(
                    system_description_id, request_number=request_number
                )
            )
            self.background_tasks.add(task)
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

        # Each storage status is a planning request: it reports the state of charge a schedule
        # has to start from, and answering it means a round trip to FlexMeasures and an
        # instruction batch to the resource manager.
        # Requests are counted so that a handler can tell its own request from a newer one and
        # drop work that answers a question already out of date (see send_instruction_batch).
        # Request order is the only signal available: in co-simulation a retrigger round shares
        # one simulated timestamp while the steering signals behind it change, so the
        # time-based throttling the TUNES handler uses cannot tell stale work from fresh work.
        request_number = self._count_request(STORAGE_STATUS_REQUESTS)

        task = asyncio.create_task(
            self.send_storage_status(message, request_number=request_number)
        )
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

    async def send_storage_status(
        self, status: FRBCStorageStatus, request_number: int | None = None
    ):
        """Record the reported state of charge, and ask for a schedule starting from it.

        :param status:         the storage status to act on
        :param request_number: which planning request this status is,
                               to be passed on to trigger_schedule and from there to
                               send_instruction_batch, so that a batch answering a superseded
                               request is dropped rather than sent
        """
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

    async def trigger_schedule(
        self, system_description_id: str, request_number: int | None = None
    ):
        """Ask FlexMeasures for a schedule, and send the resulting instructions.

        :param system_description_id: system description to base the schedule on
        :param request_number:        which request this schedule answers,
                                      to be passed on to send_instruction_batch so that a batch
                                      answering a superseded request is dropped
        """
        raise NotImplementedError()


class FRBCTest(FRBC):
    """Dummy class to simulate the triggering of a schedule."""

    async def trigger_schedule(
        self, system_description_id: str, request_number: int | None = None
    ):
        """Creates schedule consisting on just a dummy instruction

        :param system_description_id: system description to based the schedule on
        :param request_number:        which request this schedule answers
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
