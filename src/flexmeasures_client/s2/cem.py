import asyncio
import json
from asyncio import Queue
from logging import Logger
from typing import Dict, Optional, Type

import pydantic
from python_s2_protocol.common.messages import (
    Handshake,
    HandshakeResponse,
    PowerMeasurement,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
    SelectControlType,
)
from python_s2_protocol.common.schemas import ControlType

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.utils import get_reception_status, get_unique_id


class CEM(Handler):
    __version__ = "0.1.0"  # TODO: find the right version that we will use

    _resource_manager_details: ResourceManagerDetails

    control_types_handlers: Dict[ControlType, ControlTypeHandler] = dict()
    control_type = None

    _power_sensors: Dict[
        str, int
    ]  # maps the CommodityQuantity power measurement sensors to FM sensor IDs
    _fm_client: FlexMeasuresClient

    _sending_queue: Queue

    def __init__(
        self, sensor_id: int, fm_client: FlexMeasuresClient, logger: Logger = None
    ) -> None:
        """
        Customer Energy Manager (CEM)
        """
        super(CEM, self).__init__()

        self._sensor_id = sensor_id
        self._fm_client = fm_client
        self._sending_queue = Queue()
        self._power_sensors = dict()

        if not logger:
            logger = Logger(__name__)

        self._logger = logger

    def supports_control_type(self, control_type: ControlType):
        return control_type in self.resource_manager_details.available_control_types

    def register_control_type(self, control_type_handler: ControlTypeHandler):
        """
        This method registers control types.
        """

        if control_type_handler._control_type in self.control_types_handlers:
            self._logger.warning(
                "Control Type {control_type} already registered. Updating..."
            )

        self.control_types_handlers[
            control_type_handler._control_type
        ] = control_type_handler

    async def handle_message(self, message: Dict | Type[pydantic.BaseModel]):
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

        # try to handle the message with the control_type handle
        if (
            self.control_type is not None
            and (
                self.control_type
                not in [ControlType.NO_SELECTION, ControlType.NOT_CONTROLABLE]
            )
            and self.control_types_handlers[self.control_type].supports_message(message)
        ):
            response = self.control_types_handlers[self.control_type].handle_message(
                message
            )
        else:
            if self.supports_message(message):
                response = super().handle_message(message)  # run Handler.handle_message

        # add to sending queue
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
        self.control_type = control_type

    async def get_message(self):
        message = await self._sending_queue.get()
        await asyncio.sleep(1)
        return message

    async def activate_control_type(
        self, control_type: ControlType
    ) -> Optional[SelectControlType]:
        """
        This method returns a SelectControlType to enable a control type in the RM.
        """

        # check if it's trying to activate the current control_type
        if control_type == self.control_type:
            self._logger.warning(f"RM is already in `{control_type}` control type.")
            return

        # check if the RM supports the control type
        if control_type not in self._resource_manager_details.available_control_types:
            self._logger.warning(f"RM doesn not support `{control_type}` control type.")
            return

        # RM initialization succeded
        if self.control_type is not None:
            message_id = get_unique_id()

            # the callback `update_control_type` will be called upon arrival of a
            # ReceptionStatus message with status = ReceptionStatusValues.OK

            # register callback in CEM handler
            if self.control_type in [
                ControlType.NOT_CONTROLABLE,
                ControlType.NO_SELECTION,
            ]:
                self.register_success_callbacks(
                    message_id, self.update_control_type, control_type=control_type
                )
            else:  # register callback in control mode handler
                self.control_types_handlers[
                    self.control_type
                ].register_success_callbacks(
                    message_id, self.update_control_type, control_type=control_type
                )

            await self._sending_queue.put(
                SelectControlType(message_id=message_id, control_type=control_type)
            )

    @register(Handshake, "Handshake")
    def handle_handshake(self, message: Handshake):
        # TODO: check the version that the RM is using and send a
        # `selected_protocol_version` that matches the one of the RM
        # TODO: Return a TBD "CloseConnection" message to close the connection

        return HandshakeResponse(
            message_id=get_unique_id(), selected_protocol_version=self.__version__
        )

    @register(ResourceManagerDetails, "ResourceManagerDetails")
    def handle_resource_manager_details(self, message: ResourceManagerDetails):
        self._resource_manager_details = message

        if (
            not self.control_type
        ):  # initializing. TODO: check if sending resource_manager_details
            # resets control type
            self.control_type = ControlType.NO_SELECTION

        return get_reception_status(message)

    @register(PowerMeasurement, "PowerMeasurement")
    def handle_power_measurement(self, message: PowerMeasurement):
        for power_measurement in message.values:
            commodity_quantity = power_measurement.commodity_quantity

            if commodity_quantity in self._power_sensors:
                sensor_id = self._power_sensors[commodity_quantity]
            else:
                sensor_id = 1  # TODO: create a new sensor or return ReceptionStatus

            # send measurement
            self._fm_client.post_measurements(
                sensor_id,
                start=message.measurement_timestamp,
                duration="PT1H",  # TODO: not specified in S2 Protocol
                values=[power_measurement.value],
                unit=power_measurement.commodity_quantity,  # TODO: is commodity quantity a unit? for me it's just a the type of POWER # noqa: E501
            )

        return get_reception_status(message)
