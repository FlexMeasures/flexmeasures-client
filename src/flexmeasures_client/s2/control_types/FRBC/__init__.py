import asyncio

import pydantic

try:
    from s2python.common import (
        ControlType,
        InstructionStatusUpdate,
        ReceptionStatus,
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

        # --- Outstanding-work registry (2026-07, instruction-storm fix) ---
        # Supersession counters: one per request kind (e.g. "storage_status").
        # Every incoming request bumps its counter; a spawned handler task
        # records the value and no-ops if a newer request has arrived by the
        # time it runs (or, for long chains, re-checks before queueing
        # instructions). In co-simulation, retrigger rounds share one
        # simulated timestamp while steering signals change server-side, so
        # time-based throttles (live-system style, see the TUNES handler's
        # timers) cannot distinguish stale work from fresh work - only
        # request ordering can. Without this, backlogged handler tasks each
        # completed a full FlexMeasures round trip and dumped a full
        # instruction batch, hours-stale batches included (observed in vivo:
        # 96 -> 2,225 instructions per burst, ack starvation on the shared
        # sending queue, RM retry storms, and 800 s control timeouts).
        self._supersession_counters: dict[str, int] = {}
        # Instructions of the CURRENT batch, by message_id (cleared when a
        # new batch replaces them), plus any status updates the RM reports.
        # Modeled after the flexmeasures-s2 plugin's ConnectionState registry.
        self._sent_instructions: dict[str, FRBCInstruction] = {}
        self._instruction_statuses: dict[str, str] = {}
        # Whether replacing a batch also sends RevokeObject for the previous
        # batch's instructions. Off by default: not every RM implements
        # RevokeObject handling (the FLEXED co-simulation RM does not; it
        # keys instructions to its current request and drops late ones).
        # Live deployments with a compliant RM should enable this.
        self.send_revocations: bool = False

    def _bump_generation(self, key: str) -> int:
        """Register a new request of the given kind; newer requests supersede
        older ones (see _is_current_generation)."""
        generation = self._supersession_counters.get(key, 0) + 1
        self._supersession_counters[key] = generation
        return generation

    def _is_current_generation(self, key: str, generation: int) -> bool:
        return self._supersession_counters.get(key, 0) == generation

    @staticmethod
    def filter_instruction_transitions(
        instructions: list[FRBCInstruction],
    ) -> list[FRBCInstruction]:
        """Keep only instructions that CHANGE the actuator's state.

        FRBC instructions are switch commands: an instruction that repeats
        the previous instruction's (actuator, operation mode, factor) is a
        no-op for the RM, which holds its state until told otherwise (the
        flexmeasures-s2 plugin applies the same filter server-side). One
        instruction per 15-min slot of a 24 h schedule is thus typically
        90%+ redundant traffic on the serial S2 link.
        """
        filtered: list[FRBCInstruction] = []
        last_state: dict = {}
        for instruction in instructions:
            state = (
                str(instruction.operation_mode),
                float(instruction.operation_mode_factor),
            )
            actuator = str(instruction.actuator_id)
            if last_state.get(actuator) == state:
                continue
            last_state[actuator] = state
            filtered.append(instruction)
        return filtered

    async def send_instruction_batch(
        self,
        instructions: list[FRBCInstruction],
        supersession_key: str | None = None,
        generation: int | None = None,
    ) -> int:
        """Replace the outstanding instruction batch with a new one.

        Applies the transition filter, optionally revokes the previous
        batch (send_revocations), checks supersession one last time right
        before queueing (a newer request may have arrived while the
        schedule was being computed - queueing a stale batch would let the
        RM file it under its CURRENT request), sends, and records the new
        batch in the registry. Returns the number of instructions sent.
        """
        filtered = self.filter_instruction_transitions(instructions)
        if (
            supersession_key is not None
            and generation is not None
            and not self._is_current_generation(supersession_key, generation)
        ):
            self._logger.info(
                f"Dropping stale instruction batch ({len(filtered)} instructions "
                f"after transition-filtering {len(instructions)}): a newer "
                f"'{supersession_key}' request has since arrived."
            )
            return 0
        if self.send_revocations:
            for message_id in list(self._sent_instructions):
                status = self._instruction_statuses.get(message_id, "NEW")
                if status in ("NEW", "ACCEPTED"):
                    await self.send_message(
                        RevokeObject(
                            message_id=get_unique_id(),
                            object_type=RevokableObjects.FRBC_Instruction,
                            object_id=self._sent_instructions[message_id].id,
                        )
                    )
        self._sent_instructions = {}
        self._instruction_statuses = {}
        for instruction in filtered:
            await self.send_message(instruction)
            self._sent_instructions[str(instruction.message_id)] = instruction
        self._logger.debug(
            f"Sent instruction batch: {len(filtered)} instructions "
            f"({len(instructions)} before transition-filtering)."
        )
        return len(filtered)

    @register(InstructionStatusUpdate)
    def handle_instruction_status_update(
        self, message: InstructionStatusUpdate
    ) -> pydantic.BaseModel:
        """Track the RM's reported status of outstanding instructions, so a
        batch replacement only revokes instructions that are still pending."""
        self._instruction_statuses[str(message.instruction_id)] = str(
            getattr(message.status_type, "value", message.status_type)
        )
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
        # Content-hash dedupe (lifted from the TUNES handler): RMs commonly
        # re-send their (static) system description with every request
        # payload, under a fresh message_id each time. Re-processing an
        # unchanged description would spawn a redundant schedule trigger
        # per request on top of the storage status's trigger - half of the
        # instruction-storm amplification observed in co-simulation.
        message_dict = message.to_dict()
        message_dict.pop("message_id")
        system_description_hash = hash(str(message_dict))
        if getattr(self, "_last_system_description_hash", 0) == system_description_hash:
            self._logger.debug(
                "Ignoring re-sent system description (content unchanged)."
            )
            return get_reception_status(message, status=ReceptionStatusValues.OK)
        self._last_system_description_hash = system_description_hash

        system_description_id = str(message.message_id)

        # store system_description message for later
        self._system_description_history[system_description_id] = message

        # schedule trigger_schedule to run soon concurrently
        task = asyncio.create_task(self.trigger_schedule(system_description_id))
        self.background_tasks.add(
            task
        )  # important to avoid a task disappearing mid-execution.
        task.add_done_callback(self.background_tasks.discard)

        # schedule send_conversion_efficiencies to run soon concurrently
        task = asyncio.create_task(self.send_conversion_efficiencies(message))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

        return get_reception_status(message, status=ReceptionStatusValues.OK)

    def _get_operation_mode_efficiency_sensor_map(
        self, system_description: FRBCSystemDescription
    ) -> dict[str, int]:
        """
        Get a mapping of operation mode IDs to efficiency sensor IDs.

        Subclasses can override this method to provide operation mode to efficiency
        sensor mappings. Return an empty dict if there are no efficiency sensors.

        Args:
            system_description: The system description containing operation mode details.

        Returns:
            A dictionary mapping operation mode IDs to efficiency sensor IDs.
            Empty dict ({}) if there are no efficiency sensors (default).
        """
        return {}

    async def send_conversion_efficiencies(
        self, system_description: FRBCSystemDescription
    ):
        """
        Send conversion efficiencies to FlexMeasures for operation modes.

        This method sends efficiency values for each operation mode that has
        an associated efficiency sensor. Subclasses should override
        _get_operation_mode_efficiency_sensor_map() to define which operation modes
        have efficiency sensors.

        Args:
            system_description: The system description containing actuator details.
        """
        efficiency_map = self._get_operation_mode_efficiency_sensor_map(
            system_description
        )
        if not efficiency_map:
            # No efficiency sensors defined
            return

        try:
            from datetime import datetime
            from datetime import timedelta

            start = system_description.valid_from
            actuator = system_description.actuators[0]

            start_time = start.replace(
                minute=(start.minute // 15) * 15, second=0, microsecond=0
            )

            # Use a default conversion efficiency duration if not set by subclass
            duration = getattr(self, "_conversion_efficiency_duration", timedelta(hours=99))
            if isinstance(duration, str):
                # If duration is a string like "PT99H", use default timedelta
                duration = timedelta(hours=99)

            for operation_mode in actuator.operation_modes:
                sensor_id = efficiency_map.get(operation_mode.id)
                if sensor_id is None:
                    # Skip operation modes without an efficiency sensor
                    continue

                # Calculate efficiency from the last element (characteristic endpoint)
                try:
                    fill_level_scale = getattr(self, "_fill_level_scale", 1.0)
                    efficiency = (
                        1
                        * operation_mode.elements[-1].fill_rate.end_of_range
                        * fill_level_scale
                        / (operation_mode.elements[-1].power_ranges[0].end_of_range)
                    )
                    self._logger.debug(f"operation_mode.elements[-1].fill_rate.end_of_range: {operation_mode.elements[-1].fill_rate.end_of_range}")
                    self._logger.debug(f"operation_mode.elements[-1].power_ranges[0].end_of_range: {operation_mode.elements[-1].power_ranges[0].end_of_range}")
                    self._logger.debug(f"fill_level_scale: {fill_level_scale}")
                    self._logger.debug(f"efficiency: {efficiency}")
                except (IndexError, AttributeError, ZeroDivisionError) as e:
                    self._logger.debug(
                        f"Could not calculate efficiency for operation mode {operation_mode.id}: {e}"
                    )
                    continue

                try:
                    await self._fm_client.post_sensor_data(
                        sensor_id=sensor_id,
                        start=start_time,
                        prior=self.now(),
                        values=[efficiency],
                        unit="dimensionless",
                        duration=duration,
                    )
                except Exception as e:
                    self._logger.debug(
                        f"Error posting efficiency data for sensor {sensor_id}: {e}"
                    )
        except Exception as e:
            self._logger.debug(f"Error sending conversion efficiencies: {e}")

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

        # Latest-request-wins: each storage status is a (re)planning request;
        # if several arrive while earlier ones still await the event loop
        # (backlog under load, or an RM retry re-sending under a fresh
        # message_id), only the newest may do the heavy work - the older
        # requests are superseded and their tasks no-op. Subclasses whose
        # send_storage_status runs a long chain should re-check with
        # _is_current_generation before queueing instructions (see
        # send_instruction_batch).
        generation = self._bump_generation("storage_status")

        async def run_if_current():
            if not self._is_current_generation("storage_status", generation):
                self._logger.info(
                    "Skipping superseded storage-status request "
                    f"(generation {generation})."
                )
                return
            await self.send_storage_status(message)

        task = asyncio.create_task(run_if_current())
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

    @register(ReceptionStatus)
    def handle_reception_status(self, message: ReceptionStatus):
        self._logger.debug(message)
        self._logger.debug(message.subject_message_id)

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

        # put instruction into the sending queue
        await self.send_message(instruction)
