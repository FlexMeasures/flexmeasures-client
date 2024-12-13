from __future__ import annotations

import asyncio
import json
from asyncio import Queue
from logging import Logger
from typing import Dict, Optional

import pydantic
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

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.utils import get_reception_status, get_unique_id


class CEM(Handler):
    __version__ = "0.1.0"  # TODO: find the right version that we will use

    _resource_manager_details: ResourceManagerDetails

    _control_types_handlers: Dict[ControlType | None, ControlTypeHandler]
    _control_type = None
    _is_closed = True

    _power_sensors: Dict[
        str, int
    ]  # maps the CommodityQuantity power measurement sensors to FM sensor IDs

    _fm_client: FlexMeasuresClient
    _sending_queue: Queue[pydantic.BaseModel]

    def __init__(
        self, fm_client: FlexMeasuresClient, logger: Logger | None = None
    ) -> None:
        """
        Customer Energy Manager (CEM)
        """
        super(CEM, self).__init__()

        self._fm_client = fm_client
        self._sending_queue = Queue()
        self._power_sensors = dict()
        self._control_types_handlers = dict()

        if not logger:
            logger = Logger(__name__)

        self._logger = logger
        self._is_closed = False

    def supports_control_type(self, control_type: ControlType):
        return control_type in self._resource_manager_details.available_control_types

    def close(self):
        self._is_closed = True

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

        # store control_type_handler
        self._control_types_handlers[
            control_type_handler._control_type
        ] = control_type_handler

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
            response = self._control_types_handlers[self._control_type].handle_message(
                message
            )
        else:
            if self.supports_message(message):
                response = super().handle_message(message)  # run Handler.handle_message

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
            str: message in JSON forrmat
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
            self._logger.warning(f"RM doesn not support `{control_type}` control type.")
            return None

        # RM initialization succeded
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

        return HandshakeResponse(
            message_id=get_unique_id(), selected_protocol_version=self.__version__
        )

    @register(ResourceManagerDetails)
    def handle_resource_manager_details(self, message: ResourceManagerDetails):
        self._resource_manager_details = message

        if (
            not self._control_type
        ):  # initializing. TODO: check if sending resource_manager_details
            # resets control type
            self._control_type = ControlType.NO_SELECTION

        return get_reception_status(message)

    @register(PowerMeasurement)
    async def handle_power_measurement(self, message: PowerMeasurement):
        for power_measurement in message.values:
            commodity_quantity = power_measurement.commodity_quantity.value

            if commodity_quantity in self._power_sensors:
                sensor_id = self._power_sensors[commodity_quantity]
            else:
                sensor_id = 1  # TODO: create a new sensor or return ReceptionStatus

            # send measurement
            await self._fm_client.post_measurements(
                sensor_id,
                start=message.measurement_timestamp,
                duration="PT1H",  # TODO: not specified in S2 Protocol
                values=[power_measurement.value],
                unit=commodity_quantity,  # TODO: is commodity quantity a unit? for me it's just a the type of POWER # noqa: E501
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
                    message.object_id
                )

        return get_reception_status(message, ReceptionStatusValues.OK)
