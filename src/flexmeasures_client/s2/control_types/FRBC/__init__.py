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

    @register(FRBCSystemDescription)
    def handle_system_description(
        self, message: FRBCSystemDescription
    ) -> pydantic.BaseModel:
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

        # put instruction into the sending queue
        await self.send_message(instruction)
