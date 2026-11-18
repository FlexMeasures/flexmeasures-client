"""An example resource manager: a heat pump that keeps a hot-water buffer warm, and talks S2's FRBC to a CEM.

Run it by path, next to the example CEM server, as the CEM documentation shows.
It declares what the heat pump can do and what its buffer needs, and then simulates that buffer.
The simulation follows each instruction from the moment it is due.
Every so often, it reports its fill level, which triggers the CEM to plan a new schedule.

The buffer is simulated from S2's own definitions, not with the CEM's code.
That way, the simulation is an independent check on where the instructions take the storage, not a copy of the CEM's own calculation.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from s2python.common import (
    Commodity,
    CommodityQuantity,
    ControlType,
    Duration,
    EnergyManagementRole,
    Handshake,
    InstructionStatus,
    InstructionStatusUpdate,
    NumberRange,
    PowerRange,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
    RevokeObject,
    Role,
    RoleType,
    Transition,
)
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCActuatorStatus,
    FRBCFillLevelTargetProfile,
    FRBCFillLevelTargetProfileElement,
    FRBCInstruction,
    FRBCLeakageBehaviour,
    FRBCLeakageBehaviourElement,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
    FRBCUsageForecast,
    FRBCUsageForecastElement,
)

from flexmeasures_client.s2.utils import get_unique_id

#: The range the buffer's fill level should stay in, in kilowatt hours of stored heat.
FILL_LEVEL_RANGE = (2.0, 10.0)
#: Below this fill level, the water is cold enough for the heat pump to run at a COP of 4. Above it, the COP is 3.
COLD_BELOW = 6.0
#: The buffer's fill level when the simulation starts, and the most it can physically hold.
STARTING_FILL_LEVEL = 5.0
PHYSICAL_CAPACITY = 12.0
#: How often the simulated buffer reports its fill level, in seconds of wall-clock time.
STATUS_INTERVAL = float(os.getenv("S2_EXAMPLE_STATUS_INTERVAL", "300"))
#: How often the forecasts are sent again, each covering the next day, so that planning never runs out of them.
FORECAST_INTERVAL = float(os.getenv("S2_EXAMPLE_FORECAST_INTERVAL", "3600"))
ELECTRIC_POWER = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC


def heat(watts: float) -> float:
    """Convert a heat rate in watts into a fill rate in kilowatt-hours per second, which is how S2 expresses a fill rate."""
    return watts / 3.6e6


def per_second(kwh_per_hour: float) -> float:
    return kwh_per_hour / 3600


def _element(
    lowest: float,
    highest: float,
    power: tuple[float, float],
    heat_in_watts: tuple[float, float],
) -> FRBCOperationModeElement:
    return FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=lowest, end_of_range=highest),
        fill_rate=NumberRange(
            start_of_range=heat(heat_in_watts[0]), end_of_range=heat(heat_in_watts[1])
        ),
        power_ranges=[
            PowerRange(
                start_of_range=power[0],
                end_of_range=power[1],
                commodity_quantity=ELECTRIC_POWER,
            )
        ],
    )


def operation_modes() -> list[FRBCOperationMode]:
    """Return two modes: idle, and heating at 400 to 1500 W, with a COP of 4 while the buffer is cold and 3 once it is warm."""
    idle = FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="idle",
        elements=[_element(0, PHYSICAL_CAPACITY, (0, 0), (0, 0))],
        abnormal_condition_only=False,
    )
    heating = FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="heating",
        elements=[
            _element(0, COLD_BELOW, (400, 1500), (4 * 400, 4 * 1500)),
            _element(COLD_BELOW, PHYSICAL_CAPACITY, (400, 1500), (3 * 400, 3 * 1500)),
        ],
        abnormal_condition_only=False,
    )
    return [idle, heating]


def system_description(
    modes: list[FRBCOperationMode], valid_from: datetime
) -> FRBCSystemDescription:
    # S2 lists the transitions an actuator can make.
    # This heat pump declares that it can switch from either mode to the other, immediately and at no cost.
    transitions = [
        Transition(
            id=get_unique_id(),
            from_=a.id,
            to=b.id,
            start_timers=[],
            blocking_timers=[],
            abnormal_condition_only=False,
        )
        for a in modes
        for b in modes
        if a.id != b.id
    ]
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=valid_from,
        actuators=[
            FRBCActuatorDescription(
                id=get_unique_id(),
                diagnostic_label="heat pump",
                supported_commodities=[Commodity.ELECTRICITY],
                operation_modes=modes,
                transitions=transitions,
                timers=[],
            )
        ],
        storage=FRBCStorageDescription(
            diagnostic_label="hot-water buffer",
            fill_level_label="kWh of stored heat",
            provides_leakage_behaviour=True,
            provides_fill_level_target_profile=True,
            provides_usage_forecast=True,
            fill_level_range=NumberRange(
                start_of_range=FILL_LEVEL_RANGE[0], end_of_range=FILL_LEVEL_RANGE[1]
            ),
        ),
    )


def leakage_behaviour(valid_from: datetime) -> FRBCLeakageBehaviour:
    """Return the leakage behaviour: a warmer buffer loses more, 0.8 kWh a day while cold, and 1.5 kWh a day once warm."""
    return FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from=valid_from,
        elements=[
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=COLD_BELOW),
                leakage_rate=per_second(0.8 / 24),
            ),
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(
                    start_of_range=COLD_BELOW, end_of_range=PHYSICAL_CAPACITY
                ),
                leakage_rate=per_second(1.5 / 24),
            ),
        ],
    )


def expected_usage(hour: int) -> float:
    """Return the hot water drawn in a given hour of the day, in kWh: showers in the morning, washing up in the evening."""
    return {7: 3.0, 19: 1.0, 20: 1.0}.get(hour, 0.1)


def usage_forecast(start: datetime) -> FRBCUsageForecast:
    """Return a forecast for a day of hot-water use, hour by hour, from the start of the hour that start falls in."""
    start = start.replace(minute=0, second=0, microsecond=0)
    return FRBCUsageForecast(
        message_id=get_unique_id(),
        start_time=start,
        elements=[
            FRBCUsageForecastElement(
                duration=Duration(root=3600 * 1000),
                usage_rate_expected=per_second(expected_usage((start.hour + h) % 24)),
            )
            for h in range(24)
        ],
    )


def fill_level_target_profile(start: datetime) -> FRBCFillLevelTargetProfile:
    """Return a target profile within the declared range: at least 3 kWh, and at least 6 kWh in the hour before the morning showers."""
    start = start.replace(minute=0, second=0, microsecond=0)
    return FRBCFillLevelTargetProfile(
        message_id=get_unique_id(),
        start_time=start,
        elements=[
            FRBCFillLevelTargetProfileElement(
                duration=Duration(root=3600 * 1000),
                fill_level_range=NumberRange(
                    start_of_range=6.0 if (start.hour + h) % 24 == 6 else 3.0,
                    end_of_range=FILL_LEVEL_RANGE[1],
                ),
            )
            for h in range(24)
        ],
    )


@dataclass
class Buffer:
    """The simulated buffer, whose fill level changes according to S2's definitions.

    S2 gives every rate per second, in the fill level's own units.
    A fill rate raises the fill level, and usage and leakage lower it.
    An operation mode's fill rate comes from the element whose fill-level range holds the fill level.
    It is interpolated between the ends of that element's fill-rate range, using the factor.
    """

    modes: dict[str, FRBCOperationMode]
    leakage: FRBCLeakageBehaviour
    fill_level: float = STARTING_FILL_LEVEL
    mode_id: str | None = None
    factor: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def _holding(elements, fill_level: float):
        """Return the element whose range holds the fill level.

        At a shared boundary, the upper element applies.
        Outside all ranges, the nearest element applies.
        """
        lowest = min(e.fill_level_range.start_of_range for e in elements)
        highest = max(e.fill_level_range.end_of_range for e in elements)
        level = min(max(fill_level, lowest), highest)
        return max(
            (
                e
                for e in elements
                if e.fill_level_range.start_of_range
                <= level
                <= e.fill_level_range.end_of_range
            ),
            key=lambda e: (
                e.fill_level_range.start_of_range,
                e.fill_level_range.end_of_range,
            ),
        )

    def rates(self, moment: datetime) -> tuple[float, float, float]:
        """Return the fill, usage and leakage rates, each per second, at this fill level and moment."""
        fill = 0.0
        if self.mode_id is not None:
            element = self._holding(self.modes[self.mode_id].elements, self.fill_level)
            low, high = element.fill_rate.start_of_range, element.fill_rate.end_of_range
            fill = low + (high - low) * self.factor
        usage = per_second(expected_usage(moment.hour))
        leakage = self._holding(self.leakage.elements, self.fill_level).leakage_rate
        return fill, usage, leakage

    def advance(self, until: datetime, step: timedelta = timedelta(minutes=1)) -> None:
        """Move the fill level forward to a moment, a minute at a time, so that element boundaries are crossed at the right time."""
        while self.at < until:
            dt = min(step, until - self.at)
            fill, usage, leakage = self.rates(self.at)
            self.fill_level += (fill - usage - leakage) * dt.total_seconds()
            self.fill_level = min(max(self.fill_level, 0.0), PHYSICAL_CAPACITY)
            self.at += dt

    def follow(self, instruction: FRBCInstruction, moment: datetime) -> None:
        self.advance(moment)
        self.mode_id = str(instruction.operation_mode)
        self.factor = instruction.operation_mode_factor


async def send_instruction_status(
    ws, instruction_id, status: InstructionStatus
) -> None:
    """Send the CEM the status of an instruction.

    The CEM uses these statuses to decide what to revoke: it only revokes instructions that are not yet carried out.
    """
    await ws.send_str(
        InstructionStatusUpdate(
            message_id=get_unique_id(),
            instruction_id=instruction_id,
            status_type=status,
            timestamp=datetime.now(timezone.utc),
        ).model_dump_json()
    )


async def apply_at_execution_time(
    ws,
    instruction: FRBCInstruction,
    applied: dict | None = None,
    buffer: Buffer | None = None,
    on_due: Callable[[], None] | None = None,
) -> None:
    """Enter the operation mode an instruction asks for, at the moment it asks for it.

    An instruction has an execution_time.
    That is when the resource manager should be in that mode, not when the instruction happens to arrive.
    A resource manager that acts on arrival reports a mode it is not in yet.
    A customer energy manager that records that status then has a measurement of a power the device is not drawing,
    timestamped now.

    The mode it reports leaving is the mode the last executed instruction put it in, which is kept in applied.
    It is not the mode the previous instruction asked for, because that instruction may have been revoked and never carried out.
    """

    delay = (instruction.execution_time - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        print(f"WAITING: {delay:.0f}s until this instruction is due")
        await asyncio.sleep(delay)

    # From here on, the instruction is being carried out, so a revocation that arrives now is too late to stop it.
    if on_due is not None:
        on_due()
    now = datetime.now(timezone.utc)
    if buffer is not None:
        buffer.follow(instruction, now)
    status = FRBCActuatorStatus(
        message_id=get_unique_id(),
        actuator_id=instruction.actuator_id,
        active_operation_mode_id=instruction.operation_mode,
        operation_mode_factor=instruction.operation_mode_factor,
        previous_operation_mode_id=(applied or {}).get("mode"),
        # The moment the transition happened, so that what is recorded about it is placed there,
        # rather than wherever the message is read.
        transition_timestamp=now,
    )
    if applied is not None:
        applied["mode"] = instruction.operation_mode
    print("SENDING: FRBC.ActuatorStatus")
    await ws.send_str(status.model_dump_json())
    await send_instruction_status(ws, instruction.id, InstructionStatus.SUCCEEDED)


async def answer_instructions(ws, buffer: Buffer | None = None) -> None:
    """Acknowledge every instruction, and report the transition each one asks for when it is due.

    The CEM revokes an instruction when a newer plan replaces the plan it belonged to.
    A revoked instruction is not followed.
    Otherwise, the buffer would end up in whatever mode the last instruction to fire said, across every plan ever sent.
    """

    applied: dict = {"mode": None}
    pending: dict[str, asyncio.Task] = {}
    async for msg in ws:
        payload = msg.json()
        print("RECEIVED: ", payload)
        message_type = payload.get("message_type")
        if message_type == "RevokeObject":
            revocation = RevokeObject.model_validate(payload)
            task = pending.pop(str(revocation.object_id), None)
            await ws.send_str(
                ReceptionStatus(
                    subject_message_id=revocation.message_id,
                    status=ReceptionStatusValues.OK,
                ).model_dump_json()
            )
            if task is not None:
                task.cancel()
                await send_instruction_status(
                    ws, revocation.object_id, InstructionStatus.REVOKED
                )
            continue
        if message_type != "FRBC.Instruction":
            continue
        instruction = FRBCInstruction.model_validate(payload)
        await ws.send_str(
            ReceptionStatus(
                subject_message_id=instruction.message_id,
                status=ReceptionStatusValues.OK,
            ).model_dump_json()
        )
        await send_instruction_status(ws, instruction.id, InstructionStatus.ACCEPTED)
        # In a task of its own, so that an instruction due later does not hold up the ones behind it.
        key = str(instruction.id)

        def due(key: str = key) -> None:
            pending.pop(key, None)

        def forget(done: asyncio.Task, key: str = key) -> None:
            pending.pop(key, None)

        task = asyncio.create_task(
            apply_at_execution_time(ws, instruction, applied, buffer, on_due=due)
        )
        pending[key] = task
        task.add_done_callback(forget)


async def report_fill_level(
    ws, buffer: Buffer, interval: float = STATUS_INTERVAL
) -> None:
    """Report the buffer's fill level every interval, which triggers the CEM to plan a new schedule."""
    while True:
        await asyncio.sleep(interval)
        buffer.advance(datetime.now(timezone.utc))
        print(f"SENDING: FRBC.StorageStatus at {buffer.fill_level:.2f} kWh")
        await ws.send_str(
            FRBCStorageStatus(
                message_id=get_unique_id(), present_fill_level=buffer.fill_level
            ).model_dump_json()
        )


async def refresh_forecasts(
    ws, leakage: FRBCLeakageBehaviour, interval: float = FORECAST_INTERVAL
) -> None:
    """Send the leakage behaviour, the usage forecast and the comfort profile again every interval, each starting now.

    If they were sent only once, they would only cover the first day, and after that the CEM would plan without them.
    """
    while True:
        await asyncio.sleep(interval)
        now = datetime.now(timezone.utc)
        for message in (
            leakage.model_copy(
                update={"message_id": get_unique_id(), "valid_from": now}
            ),
            usage_forecast(now),
            fill_level_target_profile(now),
        ):
            print(f"SENDING: {message.message_type}")
            await ws.send_str(message.model_dump_json())


async def receive_until(ws, message_type: str) -> dict:
    """Return the next message of a type, skipping the reception statuses that arrive in between."""
    while True:
        message = (await ws.receive()).json()
        print("RECEIVED: ", message)
        if message.get("message_type") == message_type:
            return message


async def run_resource_manager(
    ws, interval: float = STATUS_INTERVAL, forecast_interval: float = FORECAST_INTERVAL
) -> Buffer:
    """Introduce the heat pump, declare what it can do and what its buffer needs, then follow instructions and report.

    The messages follow the order that S2 suggests: the system description first, then the storage's status.
    Returns the simulated buffer, so that a caller can inspect where the instructions took it.
    """
    await ws.send_str(
        Handshake(
            message_id=get_unique_id(),
            role=EnergyManagementRole.RM,
            supported_protocol_versions=["0.1.0"],
        ).model_dump_json()
    )
    await receive_until(ws, "HandshakeResponse")

    await ws.send_str(
        ResourceManagerDetails(
            message_id=get_unique_id(),
            resource_id=get_unique_id(),
            name="heat pump with a hot-water buffer",
            # It draws electricity and stores heat, so it consumes the commodity it is planned in.
            roles=[
                Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)
            ],
            instruction_processing_delay=Duration(root=1000),
            available_control_types=[
                ControlType.FILL_RATE_BASED_CONTROL,
                ControlType.NO_SELECTION,
            ],
            # It sends no PowerForecast.
            # It could measure its power, which is what the next field declares, and S2 requires at least one type there.
            provides_forecast=False,
            provides_power_measurement_types=[ELECTRIC_POWER],
        ).model_dump_json()
    )
    select = await receive_until(ws, "SelectControlType")
    await ws.send_str(
        ReceptionStatus(
            subject_message_id=select["message_id"], status=ReceptionStatusValues.OK
        ).model_dump_json()
    )

    now = datetime.now(timezone.utc)
    modes = operation_modes()
    leakage = leakage_behaviour(now)
    buffer = Buffer(modes={str(m.id): m for m in modes}, leakage=leakage, at=now)
    for message in (
        system_description(modes, now),
        leakage,
        usage_forecast(now),
        fill_level_target_profile(now),
        FRBCStorageStatus(
            message_id=get_unique_id(), present_fill_level=buffer.fill_level
        ),
    ):
        print(f"SENDING: {message.message_type}")
        await ws.send_str(message.model_dump_json())

    background = [
        asyncio.create_task(report_fill_level(ws, buffer, interval)),
        asyncio.create_task(refresh_forecasts(ws, leakage, forecast_interval)),
    ]
    try:
        await answer_instructions(ws, buffer)
    finally:
        for task in background:
            task.cancel()
    return buffer


async def main_s2():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8080/ws") as ws:
            await run_resource_manager(ws)


async def main_websocket():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8080/ws") as ws:
            try:
                async for msg in ws:
                    print("RESPONSE: ", msg.data)
                    await asyncio.sleep(1)
                    await ws.send_json(msg.data)
            except KeyboardInterrupt:
                await ws.close()


if __name__ == "__main__":
    asyncio.run(main_s2())
