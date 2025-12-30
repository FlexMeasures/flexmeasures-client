from __future__ import annotations

import asyncio
import json
import logging
import math
from asyncio import Queue
from collections import defaultdict
from datetime import datetime, timedelta
from logging import Logger
from typing import Dict, Optional

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
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.utils import (
    get_latest_compatible_version,
    get_reception_status,
    get_unique_id,
)

_LOGGER = logging.getLogger(__name__)


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

    _power_buffer: defaultdict = defaultdict(
        list
    )  # {commodity_quantity: [(timestamp, value), ...]}

    def __init__(
        self,
        fm_client: FlexMeasuresClient,
        logger: Logger | None = None,
        default_control_type: ControlType | None = None,
        timers: dict[str, datetime] | None = None,
        datastore: dict | None = None,
        power_sensor_id: dict[str, int] | None = None,
        **kwargs,
    ) -> None:
        """
        Customer Energy Manager (CEM)
        """
        super(CEM, self).__init__()

        self._fm_client = fm_client
        self._sending_queue = Queue()
        self._power_sensors = dict()
        self.power_sensor_id = power_sensor_id
        self._control_types_handlers = dict()
        self._default_control_type = default_control_type

        if not logger:
            logger = _LOGGER

        self._logger = logger
        self._is_closed = False

        self._timers = timers if timers is not None else {}
        self._datastore = datastore if datastore is not None else {}

    def _is_timer_due(self, name: str) -> bool:
        now = datetime.now()
        due_time = self._timers.get(name, now - self._minimum_measurement_period)
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
        This method handles the incoming messages to the CEM
        and routes them to their custom handler. If certain
        control type is active and there's a handler defined in both
        the control type handler as well as in the CEM, it prevails the
        on of the the control type.
        """

        response = None

        if isinstance(message, pydantic.BaseModel):
            message = json.loads(message.json())

        if isinstance(message, str):
            message = json.loads(message)

        self._logger.debug(f"Received: {message}")

        # try to handle the message with the control_type handle
        if (
            self._control_type is not None
            and (
                self._control_type
                not in [ControlType.NO_SELECTION, ControlType.NOT_CONTROLABLE]
            )
            and self._control_types_handlers[self._control_type].supports_message(
                message
            )
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

    def update_control_type(self, control_type: ControlType):
        """
        Callback function that is triggered when we receive
        a confirmation that the message has been received.
        """
        self._control_type = control_type

    async def get_message(self) -> str:
        """Call this function to get the messages to be sent to the RM

        Returns:
            str: message in JSON format
        """

        message = await self._sending_queue.get()
        await asyncio.sleep(0.3)

        # Pending for pydantic V2 to implement model.model_dump(mode="json") in
        # PR #1409 (https://github.com/pydantic/pydantic/issues/1409)
        message = json.loads(message.json())

        return message

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

            await self._sending_queue.put(
                SelectControlType(message_id=message_id, control_type=control_type)
            )
        return None

    @register(Handshake)
    def handle_handshake(self, message: Handshake):
        # TODO: check the version that the RM is using and send a
        # `selected_protocol_version` that matches the one of the RM
        # TODO: Return a TBD "CloseConnection" message to close the connection

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

        if (
            not self._control_type
        ):  # initializing. TODO: check if sending resource_manager_details
            # resets control type
            self._control_type = ControlType.NO_SELECTION

            # Activate default control type if defined
            if self._default_control_type:
                await self.activate_control_type(self._default_control_type)

        return get_reception_status(message)

    @register(PowerMeasurement)
    async def handle_power_measurement(self, message: PowerMeasurement):

        for power_measurement in message.values:
            commodity_quantity = power_measurement.commodity_quantity.value

            if (
                self.power_sensor_id is None
                and commodity_quantity == "ELECTRIC.POWER.L1"
            ):
                sensor_id = 357
            elif self.power_sensor_id:
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

            # Store the value in the buffer
            self._power_buffer[commodity_quantity].append(
                (message.measurement_timestamp, power_measurement.value)
            )

            # Compute bin
            now = datetime.now(self._timezone)
            m = self._minimum_measurement_period // pd.Timedelta(minutes=1)
            bin_end = now.replace(
                second=0, microsecond=0, minute=(now.minute // m) * m
            )  # e.g. 10:15:00
            bin_start = bin_end - self._minimum_measurement_period

            # If timer not due, just collect values
            if not self._is_timer_due(f"power_measurement_{commodity_quantity}"):
                self._logger.debug(
                    f"Collecting 5-minute average for {commodity_quantity} ({bin_start.isoformat()} – {bin_end.isoformat()})"
                )
                continue

            # Compute average of all buffered values in last 5 minutes
            buffer = self._power_buffer[commodity_quantity]
            period_values = [v for (t, v) in buffer if bin_start <= t < bin_end]

            if not period_values:
                self._logger.debug(
                    f"No samples found for {commodity_quantity} in {bin_start}–{bin_end}, skipping."
                )
                continue

            avg_value = sum(period_values) / len(period_values)
            self._logger.debug(
                f"Posting 5-minute average for {commodity_quantity}: "
                f"{avg_value} ({bin_start.isoformat()} – {bin_end.isoformat()})"
            )

            # Send measurement
            try:
                await self._fm_client.post_sensor_data(
                    sensor_id,
                    start=bin_start.isoformat(),
                    duration=self._minimum_measurement_period.isoformat(),  # TODO: not specified in S2 Protocol
                    values=[avg_value],
                    unit=get_commodity_unit(commodity_quantity),
                )
            except Exception as e:
                self._logger.warning(
                    f"POSTing power measurement failed with error: {e}"
                )

            # Keep only samples newer than this bin (for next period)
            self._power_buffer[commodity_quantity] = [
                (t, v) for (t, v) in buffer if t >= bin_end
            ]

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
                    message.object_id
                )

        return get_reception_status(message, ReceptionStatusValues.OK)

    async def send_message(self, message):
        self._logger.debug(f"Sent: {message}")
        await self._sending_queue.put(message)


def get_commodity_unit(commodity_quantity) -> str:
    if "POWER" in commodity_quantity:
        return "kW"
    if "FLOW_RATE" in commodity_quantity:
        return "m³/h"
    if "TEMPERATURE" in commodity_quantity:
        return "°C"
    return ""
