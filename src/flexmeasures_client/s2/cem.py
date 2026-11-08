from __future__ import annotations

import asyncio
import json
import logging
import math
from asyncio import Queue
from datetime import datetime, timedelta
from logging import Logger
from typing import Awaitable, Callable, Dict, Optional, Protocol

import pandas as pd
import pydantic

try:
    from s2python.common import (
        ControlType,
        Handshake,
        HandshakeResponse,
        PowerMeasurement,
        ReceptionStatus,
        ReceptionStatusValues,
        ResourceManagerDetails,
        RevokeObject,
        SelectControlType,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.exceptions import ContentTypeError
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.clock import Clock, SimulatedClock
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.measurements import MeasurementRecorder
from flexmeasures_client.s2.utils import (
    get_latest_compatible_version,
    get_reception_status,
    get_unique_id,
)
from flexmeasures_client.s2.wrapper import MetaData

_LOGGER = logging.getLogger(__name__)


class ConfiguredHandler(Protocol):
    """What a site configurator returns: the handler for a resource manager, and its measured-power sensors."""

    @property
    def handler(self) -> ControlTypeHandler: ...

    @property
    def power_sensor_ids(self) -> dict[str, int]: ...

    @property
    def asset_id(self) -> int: ...


#: A callable that takes a resource manager's details and gives it its own site and handler (see flexmeasures_client.s2.site).
SiteConfigurator = Callable[[ResourceManagerDetails], Awaitable[ConfiguredHandler]]

#: Maps the prefix of a message type to the control type that its messages belong to.
CONTROL_TYPE_BY_MESSAGE_PREFIX: dict[str, ControlType] = {
    "FRBC.": ControlType.FILL_RATE_BASED_CONTROL,
    "PEBC.": ControlType.POWER_ENVELOPE_BASED_CONTROL,
    "PPBC.": ControlType.POWER_PROFILE_BASED_CONTROL,
    "OMBC.": ControlType.OPERATION_MODE_BASED_CONTROL,
    "DDBC.": ControlType.DEMAND_DRIVEN_BASED_CONTROL,
}


class CEM(Handler):
    __version__ = "0.0.2-beta"

    _resource_manager_details: ResourceManagerDetails

    _control_types_handlers: Dict[ControlType | None, ControlTypeHandler]
    _control_type = None
    _is_closed = True
    _default_control_type: ControlType | None

    _power_sensors: Dict[
        str, int
    ]  # maps the CommodityQuantity power measurement sensors to FM sensor IDs

    _fm_client: FlexMeasuresClient
    _sending_queue: Queue[pydantic.BaseModel]

    _timers: dict[str, datetime]
    _datastore: dict
    _minimum_measurement_period: pd.Timedelta = pd.Timedelta(minutes=5)

    def __init__(
        self,
        fm_client: FlexMeasuresClient,
        logger: Logger | None = None,
        default_control_type: ControlType | None = None,
        timers: dict[str, datetime] | None = None,
        datastore: dict | None = None,
        power_sensor_id: dict[str, int] | None = None,
        site_configurator: SiteConfigurator | None = None,
        measurement_recorder: MeasurementRecorder | None = None,
        clock: Clock | None = None,
        accept_simulation_metadata: bool = False,
        **kwargs,
    ) -> None:
        """
        Customer Energy Manager (CEM)

        :param site_configurator:           gives a resource manager its own site and handler when it connects.
                                            It is called with the resource manager's details, in the background (see FRBCSiteConfigurator).
                                            Until the handler is registered, the CEM responds to the resource manager's messages with TEMPORARY_ERROR.
                                            Without a site configurator, the handlers registered up front serve every resource manager.
                                            By default, there is none.
        :param measurement_recorder:        records the resource manager's measured power.
                                            By default, the CEM creates its own recorder.
                                            A server that creates a CEM per connection should hand each CEM the same recorder.
                                            Then a resource manager that reconnects does not get its measurements recorded twice.
        :param clock:                       the clock that this CEM and every handler it registers read the time from.
                                            By default, this is the wall clock.
        :param accept_simulation_metadata:  whether to accept messages wrapped with a simulated time, as a co-simulation sends them,
                                            and set the clock to that time (see flexmeasures_client.s2.wrapper.S2Wrapper).
                                            This is off by default, because the wrapper is not part of S2,
                                            and a live CEM should not let a resource manager set its time.
                                            When it is off, a resource manager that wraps its messages from its handshake on is refused with INVALID_MESSAGE, and cannot connect.
                                            When it is on, the clock must be a SimulatedClock.
                                            If no clock is given, a SimulatedClock is created.
        """
        super(CEM, self).__init__()
        if accept_simulation_metadata:
            clock = clock if clock is not None else SimulatedClock()
            if not isinstance(clock, SimulatedClock):
                raise ValueError(
                    "A CEM that accepts simulation metadata sets its clock from it, so it needs a SimulatedClock."
                )
        if clock is not None:
            self._clock = clock
        self._accept_simulation_metadata = accept_simulation_metadata

        self._fm_client = fm_client
        self._sending_queue = Queue()
        self._power_sensors = dict()
        self.power_sensor_id = power_sensor_id
        self._control_types_handlers = dict()
        self._site_configurator = site_configurator
        self._measurement_recorder = measurement_recorder or MeasurementRecorder(
            fm_client, logger=logger
        )
        self._configuring: asyncio.Task | None = None
        #: The control type this CEM asked the resource manager to select, and the id of that request. Reset once confirmed.
        self._selecting: tuple[ControlType, str] | None = None
        self._default_control_type = default_control_type

        if not logger:
            logger = _LOGGER

        self._logger = logger
        self._is_closed = False

        self._timers = timers if timers is not None else {}
        self._datastore = datastore if datastore is not None else {}

    def _is_timer_due(self, name: str) -> bool:
        # Use this handler's clock, so that a co-simulation's timers run on simulated time.
        now = self.now()
        due_time = self._timers.get(name, now - self._minimum_measurement_period)
        if due_time.tzinfo is None:
            # A timer without a timezone used to be compared with the host's local time, so keep reading it as local time.
            due_time = due_time.astimezone(self._timezone)
        if due_time <= now:
            # Get total seconds of the period
            period_seconds = self._minimum_measurement_period.total_seconds()

            # Seconds since start of the hour
            seconds_since_hour = now.minute * 60 + now.second + now.microsecond / 1e6

            # Ceil to next multiple of period_seconds
            next_tick_seconds = (
                math.ceil(seconds_since_hour / period_seconds) * period_seconds
            )

            # Compute next due datetime
            next_due = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                seconds=next_tick_seconds
            )
            # On a period boundary, the ceiling equals the current moment.
            # A clock that stands still (as a simulated clock does between messages) would then find the timer due on every call.
            if next_due <= now:
                next_due += self._minimum_measurement_period
            self._timers[name] = next_due
            return True
        else:
            self._logger.debug(
                f"Timer for {name} is not due until {self._timers[name]}"
            )
            return False

    def supports_control_type(self, control_type: ControlType):
        return control_type in self._resource_manager_details.available_control_types

    async def close(self):
        self._is_closed = True
        if self._configuring is not None:
            self._configuring.cancel()

        for control_type, handler in self._control_types_handlers.items():
            self._logger.debug(f"Closing handler for {control_type}")
            await handler.close()

    def is_closed(self):
        return self._is_closed

    @property
    def control_type(self):
        return self._control_type

    def register_control_type(self, control_type_handler: ControlTypeHandler):
        """
        This method registers control types.
        """

        # skip registering if there's a handler already registered for
        # the same control type
        if control_type_handler._control_type in self._control_types_handlers:
            self._logger.warning(
                "Control Type {control_type} already registered. Updating..."
            )

        # add fm_client to control_type handler
        control_type_handler._fm_client = self._fm_client

        # The handler uses the CEM's clock, which a co-simulation sets.
        control_type_handler._clock = self._clock

        # add sending queue
        control_type_handler._sending_queue = self._sending_queue

        # Add logger
        control_type_handler._logger = self._logger

        # store control_type_handler
        self._control_types_handlers[control_type_handler._control_type] = (
            control_type_handler
        )

    async def handle_message(self, message: Dict | pydantic.BaseModel | str):
        """
        This method handles the incoming messages to the CEM and routes them to their custom handler.
        If a certain control type is active and there's a handler defined in both
        the control type handler and in the CEM, then the one defined in the control type prevails.
        """

        response: pydantic.BaseModel | None = None

        if isinstance(message, pydantic.BaseModel):
            message = json.loads(message.json())

        if isinstance(message, str):
            message = json.loads(message)
        assert isinstance(
            message, dict
        )  # from here on, the message is a dict, whatever form it came in

        if "metadata" in message and "message" in message:
            unwrapped = self._unwrap(message)
            if unwrapped is None:
                return
            message = unwrapped

        self._logger.debug(f"Received: {message}")
        self._activate_implicitly(message)

        # try to handle the message with the control_type handle
        # A control type may have no handler yet, for example while the resource manager's site is being configured.
        # Its messages are then left unhandled, so the CEM responds with a TEMPORARY_ERROR, and the resource manager retries.
        handler = self._control_types_handlers.get(self._control_type)
        if handler is None and self._control_type is not None:
            self._ensure_configuring()
        if (
            self._control_type is not None
            and (
                self._control_type
                not in [ControlType.NO_SELECTION, ControlType.NOT_CONTROLABLE]
            )
            and handler is not None
            and handler.supports_message(message)
        ):
            response = await self._control_types_handlers[
                self._control_type
            ].handle_message(message)
        else:
            if self.supports_message(message):
                response = await super().handle_message(
                    message
                )  # run Handler.handle_message

        # TODO: handle exceptions of handle message using Exceptions
        if response is None and message.get("message_type") not in ["ReceptionStatus"]:
            # case where none of the handlers support the message type
            response = ReceptionStatus(
                subject_message_id=message.get("message_id"),
                status=ReceptionStatusValues.TEMPORARY_ERROR,
            )

        if response is not None:
            await self._sending_queue.put(response)

    def _unwrap(self, wrapped: dict) -> dict | None:
        """Return the S2 message inside a co-simulation's wrapper, and set the clock to the wrapper's simulated time.

        The message is refused in three cases:
        this CEM does not accept the wrapper, the time cannot be read, or there is no message inside.
        Then the clock is left unchanged, and None is returned, so that nothing is handled.
        """
        inner = wrapped["message"]
        reason = None
        if not isinstance(inner, dict) or not isinstance(
            inner.get("message_type"), str
        ):
            reason = "There is no S2 message inside this wrapper."
            inner = inner if isinstance(inner, dict) else {}
        elif not self._accept_simulation_metadata:
            reason = (
                "This CEM does not accept messages wrapped with simulation metadata."
            )
        else:
            try:
                moment = MetaData.model_validate(wrapped["metadata"]).dt
                assert isinstance(self._clock, SimulatedClock)
                self._clock.set(moment)
            except (pydantic.ValidationError, ValueError, TypeError) as exc:
                reason = f"The simulated time in this message's metadata cannot be used: {exc}"
        if reason is None:
            return inner
        self._logger.warning("%s Refusing %s.", reason, inner.get("message_type"))
        if inner.get("message_id") is not None:
            self._sending_queue.put_nowait(
                ReceptionStatus(
                    subject_message_id=inner["message_id"],
                    status=ReceptionStatusValues.INVALID_MESSAGE,
                    diagnostic_label=reason,
                )
            )
        return None

    def _activate_implicitly(self, message: dict) -> None:
        """Treat a message of the control type being selected as the resource manager's acceptance of that control type.

        A resource manager only sends a control type's messages once that control type is active on its side.
        Suppose such a message arrives for the control type this CEM asked for, while the CEM still waits for the confirmation.
        Then the resource manager is already working in that control type.
        In #188, a resource manager reconnected during setup in a co-simulation, and kept sending FRBC messages.
        The CEM stayed in NO_SELECTION, responded to each message with a TEMPORARY_ERROR, and the resource manager retried.
        It was not established whether the confirmation was lost or never sent.
        This method only activates the control type being selected, never another control type that a message happens to belong to.
        After that, the CEM no longer waits for the pending confirmation.
        """
        if self._control_type != ControlType.NO_SELECTION or self._selecting is None:
            return
        control_type, selection_message_id = self._selecting
        message_type = message.get("message_type")
        if not isinstance(message_type, str):
            return
        prefix = next(
            (
                p
                for p, ct in CONTROL_TYPE_BY_MESSAGE_PREFIX.items()
                if ct == control_type
            ),
            None,
        )
        if prefix is None or not message_type.startswith(prefix):
            return
        if control_type not in self._control_types_handlers:
            return
        self._logger.warning(
            "Received %s before the resource manager confirmed the selection of %s; taking it as the confirmation.",
            message_type,
            control_type.value,
        )
        self.success_callbacks.pop(selection_message_id, None)
        self.failure_callbacks.pop(selection_message_id, None)
        self.update_control_type(control_type)

    def update_control_type(self, control_type: ControlType):
        """
        Callback function that is triggered when we receive
        a confirmation that the message has been received.
        """
        self._control_type = control_type
        self._selecting = None

    async def get_message(self) -> dict:
        """Call this function to get the messages to be sent to the RM

        Returns:
            dict: the message, as JSON-serialisable Python objects
        """

        message = await self._sending_queue.get()
        return message.model_dump(mode="json")

    async def activate_control_type(
        self, control_type: ControlType
    ) -> Optional[SelectControlType]:
        """
        This method returns a SelectControlType to enable a control type in the RM.
        """

        # check if it's trying to activate the current control_type
        if control_type == self._control_type:
            self._logger.warning(f"RM is already in `{control_type}` control type.")
            return None

        # check if the RM supports the control type
        if control_type not in self._resource_manager_details.available_control_types:
            self._logger.warning(f"RM does not support `{control_type}` control type.")
            return None

        # RM initialization succeeded
        if self._control_type is not None:
            message_id = get_unique_id()

            # the callback `update_control_type` will be called upon arrival of a
            # ReceptionStatus message with status = ReceptionStatusValues.OK

            # register callback in CEM handler
            if self._control_type in [
                ControlType.NOT_CONTROLABLE,
                ControlType.NO_SELECTION,
            ]:
                self.register_success_callbacks(
                    message_id, self.update_control_type, control_type=control_type
                )
            else:  # register callback in control mode handler
                self._control_types_handlers[
                    self._control_type
                ].register_success_callbacks(
                    message_id, self.update_control_type, control_type=control_type
                )

            self._selecting = (control_type, message_id)
            await self._sending_queue.put(
                SelectControlType(message_id=message_id, control_type=control_type)
            )
        return None

    @register(Handshake)
    async def handle_handshake(self, message: Handshake):
        # TODO: check the version that the RM is using and send a
        # `selected_protocol_version` that matches the one of the RM
        # TODO: Return a TBD "CloseConnection" message to close the connection
        await self.send_message(get_reception_status(message, ReceptionStatusValues.OK))

        latest_compatible_version = get_latest_compatible_version(
            message.supported_protocol_versions,
            self.__version__,
            self._logger,
        )

        return HandshakeResponse(
            message_id=get_unique_id(),
            selected_protocol_version=str(latest_compatible_version),
        )

    @register(ResourceManagerDetails)
    async def handle_resource_manager_details(self, message: ResourceManagerDetails):
        self._resource_manager_details = message
        self._ensure_configuring()

        if (
            not self._control_type
        ):  # initializing. TODO: check if sending resource_manager_details
            # resets control type
            self._control_type = ControlType.NO_SELECTION

            # Activate default control type if defined
            if self._default_control_type:
                await self.activate_control_type(self._default_control_type)

        return get_reception_status(message)

    def _ensure_configuring(self) -> None:
        """Set up the resource manager's site in the background, unless that is already done or under way.

        Setting up a site takes a few requests to FlexMeasures, and meanwhile the resource manager waits for a reception status.
        That is why this runs in the background.
        If the setup fails, it is tried again on the next message that needs it.
        Meanwhile, the CEM responds to the resource manager's messages with TEMPORARY_ERROR, and the resource manager retries them.
        """
        if (
            self._site_configurator is None
            or self._control_types_handlers
            or (self._configuring is not None and not self._configuring.done())
            or not hasattr(self, "_resource_manager_details")
        ):
            return
        self._configuring = asyncio.create_task(
            self._configure_site(self._resource_manager_details)
        )
        self._configuring.add_done_callback(self._report_configuring)

    def _report_configuring(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        self._logger.error(
            "Could not set a site up for resource manager %r; trying again on its next message.",
            self._resource_manager_details.name,
            exc_info=task.exception(),
        )

    async def _configure_site(self, details: ResourceManagerDetails) -> None:
        """Give the resource manager its site, and register the handler wired to it."""
        assert self._site_configurator is not None
        site = await self._site_configurator(details)
        self.power_sensor_id = {**(self.power_sensor_id or {}), **site.power_sensor_ids}
        self.register_control_type(site.handler)
        self._logger.info(
            "Resource manager %r is served on site %s.", details.name, site.asset_id
        )

    @register(PowerMeasurement)
    async def handle_power_measurement(self, message: PowerMeasurement):

        if self._site_configurator is not None and not self._control_types_handlers:
            # The site is still being set up, so there is no sensor to record on yet. Ask the resource manager to resend the message.
            self._ensure_configuring()
            return get_reception_status(message, ReceptionStatusValues.TEMPORARY_ERROR)

        taken: list[tuple[int, float]] = []
        for power_measurement in message.values:
            commodity_quantity = power_measurement.commodity_quantity.value

            # A measurement without a sensor is not recorded.
            # An ELECTRIC.POWER.L1 measurement used to go to sensor 357, which could belong to any site on the server.
            sensor_id = (self.power_sensor_id or {}).get(commodity_quantity)
            if sensor_id is None:
                self._logger.debug(
                    "No power sensor set up for %s. Ignoring measurement %s at %s.",
                    commodity_quantity,
                    power_measurement.value,
                    message.measurement_timestamp,
                )
                continue
            taken.append((sensor_id, power_measurement.value))

        # Get every sensor ready before taking any value.
        # Otherwise, part of a message could be taken twice, if the resource manager is asked to send it again.
        try:
            for sensor_id, _ in taken:
                await self._measurement_recorder.resolution(sensor_id)
        except (ValueError, ConnectionError, ContentTypeError) as exc:
            self._logger.warning(
                "Could not take the measured power at %s, so the resource manager is asked to send it again: %s",
                message.measurement_timestamp,
                exc,
            )
            return get_reception_status(message, ReceptionStatusValues.TEMPORARY_ERROR)
        for sensor_id, watts in taken:
            await self._measurement_recorder.add(
                sensor_id, message.measurement_timestamp, watts, now=self.now()
            )

        return get_reception_status(message)

    @register(RevokeObject)
    def handle_revoke_object(self, message: RevokeObject):
        """
        Stores the revoked object ID into the objects_revoked list
        """

        control_types = {
            "FRBC": ControlType.FILL_RATE_BASED_CONTROL,
            "DDBC": ControlType.DEMAND_DRIVEN_BASED_CONTROL,
            "PEBC": ControlType.POWER_ENVELOPE_BASED_CONTROL,
            "OMBC": ControlType.OPERATION_MODE_BASED_CONTROL,
            "PPBC": ControlType.POWER_PROFILE_BASED_CONTROL,
        }

        for initials, control_type in control_types.items():
            if initials in str(message.object_type):
                self._control_types_handlers[control_type].revoke_message(
                    str(message.object_id)
                )

        return get_reception_status(message, ReceptionStatusValues.OK)

    async def send_message(self, message):
        self._logger.debug(f"Sent: {message}")
        await self._sending_queue.put(message)
