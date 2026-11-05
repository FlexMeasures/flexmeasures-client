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
    """What a site configurator gives back: the handler for a resource manager, and its measured-power sensors."""

    @property
    def handler(self) -> ControlTypeHandler: ...

    @property
    def power_sensor_ids(self) -> dict[str, int]: ...

    @property
    def asset_id(self) -> int: ...


#: Gives a resource manager a site and a handler of its own, from its details (see flexmeasures_client.s2.site).
SiteConfigurator = Callable[[ResourceManagerDetails], Awaitable[ConfiguredHandler]]

#: The control type each family of control-type messages belongs to, by the prefix of its message type.
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

        :param site_configurator:           where a resource manager gets a site and a handler of its own on connecting;
                                            called with its details, in the background (see FRBCSiteConfigurator).
                                            Until the handler is registered, its messages are answered with TEMPORARY_ERROR.
                                            Without one, the handlers registered up front serve every resource manager.
        :param measurement_recorder:        where the resource manager's measured power is recorded;
                                            one of its own by default. A server that makes a CEM per connection
                                            hands each the same one, so a resource manager that reconnects is not recorded twice.
        :param clock:                       the time this CEM and every handler it registers go by;
                                            the wall clock by default.
        :param accept_simulation_metadata:  whether to take messages wrapped with a simulated time, as a co-simulation sends them,
                                            and set the clock to that time (see flexmeasures_client.s2.wrapper.S2Wrapper).
                                            Off by default, since the wrapper is not part of S2,
                                            and a live CEM should not let a resource manager set its time.
                                            A resource manager that wraps its messages, from its handshake on,
                                            is then refused with INVALID_MESSAGE and cannot connect.
                                            On, the clock has to be a SimulatedClock, and one is made where none is given.
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
        #: The control type this CEM asked the resource manager to select, and the id of that message, until confirmed.
        self._selecting: tuple[ControlType, str] | None = None
        self._default_control_type = default_control_type

        if not logger:
            logger = _LOGGER

        self._logger = logger
        self._is_closed = False

        self._timers = timers if timers is not None else {}
        self._datastore = datastore if datastore is not None else {}

    def _is_timer_due(self, name: str) -> bool:
        # By this handler's clock, so that a co-simulation's timers run on simulated time.
        now = self.now()
        due_time = self._timers.get(name, now - self._minimum_measurement_period)
        if due_time.tzinfo is None:
            # A timer handed in without a zone was compared with the host's local time, so it is read as that, as before.
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
            # On a period boundary the ceiling is the moment itself,
            # and a clock that stands still, as a simulated one does between messages, then found the timer due on every call.
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

        # The handler goes by the CEM's time, which a co-simulation sets.
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
        )  # what a message is from here on, whatever form it came in

        if "metadata" in message and "message" in message:
            unwrapped = self._unwrap(message)
            if unwrapped is None:
                return
            message = unwrapped

        self._logger.debug(f"Received: {message}")
        self._activate_implicitly(message)

        # try to handle the message with the control_type handle
        # A control type without a handler yet, as while a resource manager's site is being configured,
        # leaves its messages unhandled, so they are answered with a TEMPORARY_ERROR for the resource manager to retry.
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
        """The S2 message inside a co-simulation's wrapper, with the clock set to the simulated time it carries.

        Where this CEM does not accept the wrapper, the time cannot be read, or there is no message inside,
        the message is refused, the clock is left as it was, and None is returned so that nothing is handled.
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
        """Take a message of the control type this CEM is selecting as the resource manager's acceptance of it.

        A resource manager sends a control type's messages only once that control type is active on its side.
        So where one arrives for the control type this CEM asked for, while the CEM still waits for the confirmation,
        the resource manager is working in it already. #188 saw the CEM stay in NO_SELECTION while a resource manager,
        reconnecting during setup in a co-simulation, kept sending FRBC messages, each answered with a TEMPORARY_ERROR
        and retried; whether the confirmation was lost or never sent was not established.
        Only the control type being selected is activated, never another one a message happens to belong to,
        and its pending confirmation is then no longer waited for.
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
        """Set the resource manager's site up in the background, unless that is done or under way.

        In the background, since a site takes a few requests to FlexMeasures to set up,
        and the resource manager waits for a reception status meanwhile.
        A setup that failed is tried again on the next message that needs it,
        while the resource manager's messages are answered with TEMPORARY_ERROR, which it retries.
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
            # Its site is still being set up, so there is nowhere to record it yet: the resource manager is asked to resend it.
            self._ensure_configuring()
            return get_reception_status(message, ReceptionStatusValues.TEMPORARY_ERROR)

        for power_measurement in message.values:
            commodity_quantity = power_measurement.commodity_quantity.value

            # Without a sensor for it, a measurement is not recorded.
            # It used to go to sensor 357 for ELECTRIC.POWER.L1, a sensor of whichever site on the server had that id.
            if self.power_sensor_id:
                s_id = self.power_sensor_id.get(commodity_quantity)
                if s_id is None:
                    # TODO: create a new sensor or return ReceptionStatus
                    self._logger.debug(
                        f"No power sensor set up for {commodity_quantity}. Ignoring measurement {power_measurement.value} at {message.measurement_timestamp}."
                    )
                    continue
                sensor_id = s_id
            else:
                self._logger.warning(
                    f"No power sensor IDs set up. Ignoring measurement {power_measurement.value} at {message.measurement_timestamp}."
                )
                continue

            try:
                await self._measurement_recorder.add(
                    sensor_id,
                    message.measurement_timestamp,
                    power_measurement.value,
                    now=self.now(),
                )
            except (ValueError, ConnectionError, ContentTypeError) as exc:
                # Not taken at all, as when the sensor's resolution could not be read: the resource manager is asked to resend it.
                self._logger.warning(
                    "Could not take measured power for %s at %s: %s",
                    commodity_quantity,
                    message.measurement_timestamp,
                    exc,
                )
                return get_reception_status(
                    message, ReceptionStatusValues.TEMPORARY_ERROR
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
