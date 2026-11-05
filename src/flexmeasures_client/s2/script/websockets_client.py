"""An example resource manager: a heat pump keeping a hot-water buffer warm, speaking S2's FRBC to a CEM.

Run it by path, next to the example CEM server, as the CEM documentation shows.
It declares what the heat pump can do and what its buffer needs, then simulates that buffer:
it follows each instruction from the moment it is due, and reports its fill level every so often,
which is what asks the CEM for a new schedule.

The buffer is simulated from S2's own definitions, and not with the CEM's code,
so that it is a second opinion on where the instructions take the storage rather than a copy of the first.
"""

from __future__ import annotations

import asyncio
import os
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
    NumberRange,
    PowerRange,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
    Role,
    RoleType,
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
#: Below this fill level the water is cold enough for the heat pump to run at a COP of 4; above it, 3.
COLD_BELOW = 6.0
#: What the buffer holds when the simulation starts, and what it can physically hold at most.
STARTING_FILL_LEVEL = 5.0
PHYSICAL_CAPACITY = 12.0
#: How often the simulated buffer reports its fill level, in seconds of wall-clock time.
STATUS_INTERVAL = float(os.getenv("S2_EXAMPLE_STATUS_INTERVAL", "300"))
ELECTRIC_POWER = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC


def heat(watts: float) -> float:
    """A rate of heat in watts as a fill rate in kilowatt hours per second, which is how S2 states a fill rate."""
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
    """Idle, or heating at anything from 400 to 1500 W, at a COP of 4 while the buffer is cold and 3 once it is warm."""
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
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=valid_from,
        actuators=[
            FRBCActuatorDescription(
                id=get_unique_id(),
                diagnostic_label="heat pump",
                supported_commodities=[Commodity.ELECTRICITY],
                operation_modes=modes,
                transitions=[],
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
    """A warmer buffer loses more: 0.8 kWh a day while cold, 1.5 kWh a day once warm."""
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
    """Hot water drawn in a given hour of the day, in kWh: showers in the morning, washing up in the evening."""
    return {7: 3.0, 19: 1.0, 20: 1.0}.get(hour, 0.1)


def usage_forecast(start: datetime) -> FRBCUsageForecast:
    """A day of hot-water use, hour by hour, from the hour start falls in."""
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
    """Kept within the declared range, and at least 6 kWh in the hour before the morning showers."""
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
    """The simulated buffer: where its fill level goes, following S2's definitions.

    S2 states every rate per second, in the fill level's own units:
    a fill rate raises the fill level, and usage and leakage lower it.
    An operation mode's fill rate is interpolated between the ends of its element's range by the factor,
    from the element whose fill-level range holds the fill level.
    """

    modes: dict[str, FRBCOperationMode]
    leakage: FRBCLeakageBehaviour
    fill_level: float = STARTING_FILL_LEVEL
    mode_id: str | None = None
    factor: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def _holding(elements, fill_level: float):
        """The element whose range holds the fill level, the upper one at a shared boundary, the nearest outside."""
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
        """Fill, usage and leakage, each per second, at this fill level and moment."""
        fill = 0.0
        if self.mode_id is not None:
            element = self._holding(self.modes[self.mode_id].elements, self.fill_level)
            low, high = element.fill_rate.start_of_range, element.fill_rate.end_of_range
            fill = low + (high - low) * self.factor
        usage = per_second(expected_usage(moment.hour))
        leakage = self._holding(self.leakage.elements, self.fill_level).leakage_rate
        return fill, usage, leakage

    def advance(self, until: datetime, step: timedelta = timedelta(minutes=1)) -> None:
        """Move the fill level forward to a moment, a minute at a time, so that an element boundary is crossed in time."""
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


async def apply_at_execution_time(
    ws,
    instruction: FRBCInstruction,
    previous_operation_mode_id=None,
    buffer: Buffer | None = None,
) -> None:
    """Enter the operation mode an instruction asks for, at the moment it asks for it.

    An instruction carries an execution_time,
    which is when the resource manager is to be in that mode, not when the instruction happens to arrive.
    A resource manager that answers on arrival reports a mode it is not in yet,
    and a customer energy manager recording that status has a measurement of a power the device is not drawing,
    timestamped now.
    """

    delay = (instruction.execution_time - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        print(f"WAITING: {delay:.0f}s until this instruction is due")
        await asyncio.sleep(delay)

    now = datetime.now(timezone.utc)
    if buffer is not None:
        buffer.follow(instruction, now)
    status = FRBCActuatorStatus(
        message_id=get_unique_id(),
        actuator_id=instruction.actuator_id,
        active_operation_mode_id=instruction.operation_mode,
        operation_mode_factor=instruction.operation_mode_factor,
        previous_operation_mode_id=previous_operation_mode_id,
        # The moment the transition happened, so that what is recorded about it is placed there,
        # rather than wherever the message is read.
        transition_timestamp=now,
    )
    print("SENDING: FRBC.ActuatorStatus")
    await ws.send_str(status.model_dump_json())


async def answer_instructions(ws, buffer: Buffer | None = None) -> None:
    """Acknowledge every instruction, and report the transition each one asks for when it is due."""

    active_operation_mode_id = None
    async for msg in ws:
        payload = msg.json()
        print("RECEIVED: ", payload)
        if payload.get("message_type") != "FRBC.Instruction":
            continue
        instruction = FRBCInstruction.model_validate(payload)
        await ws.send_str(
            ReceptionStatus(
                subject_message_id=instruction.message_id,
                status=ReceptionStatusValues.OK,
            ).model_dump_json()
        )
        # In a task of its own, so that an instruction due later does not hold up the ones behind it.
        asyncio.create_task(
            apply_at_execution_time(ws, instruction, active_operation_mode_id, buffer)
        )
        active_operation_mode_id = instruction.operation_mode


async def report_fill_level(
    ws, buffer: Buffer, interval: float = STATUS_INTERVAL
) -> None:
    """Report where the buffer is every interval, which is what asks the CEM for a new schedule."""
    while True:
        await asyncio.sleep(interval)
        buffer.advance(datetime.now(timezone.utc))
        print(f"SENDING: FRBC.StorageStatus at {buffer.fill_level:.2f} kWh")
        await ws.send_str(
            FRBCStorageStatus(
                message_id=get_unique_id(), present_fill_level=buffer.fill_level
            ).model_dump_json()
        )


async def receive_until(ws, message_type: str) -> dict:
    """The next message of a type, past the reception statuses that arrive in between."""
    while True:
        message = (await ws.receive()).json()
        print("RECEIVED: ", message)
        if message.get("message_type") == message_type:
            return message


async def run_resource_manager(ws, interval: float = STATUS_INTERVAL) -> Buffer:
    """Introduce the heat pump, declare what it can do and what its buffer needs, then follow and report.

    In the order S2 suggests: the system description first, then the storage's status.
    Returns the simulated buffer, for whoever wants to look at where the instructions took it.
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
            roles=[Role(role=RoleType.ENERGY_STORAGE, commodity=Commodity.ELECTRICITY)],
            instruction_processing_delay=Duration(root=1000),
            available_control_types=[
                ControlType.FILL_RATE_BASED_CONTROL,
                ControlType.NO_SELECTION,
            ],
            provides_forecast=True,
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

    reporting = asyncio.create_task(report_fill_level(ws, buffer, interval))
    try:
        await answer_instructions(ws, buffer)
    finally:
        reporting.cancel()
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
