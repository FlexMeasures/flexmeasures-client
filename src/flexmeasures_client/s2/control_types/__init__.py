from __future__ import annotations

from asyncio import Queue
from logging import Logger, getLogger

from pydantic import BaseModel
from s2python.common import (
    ControlType,
    InstructionStatus,
    InstructionStatusUpdate,
    ReceptionStatusValues,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.utils import SizeLimitOrderedDict, get_reception_status


class ControlTypeHandler(Handler):
    _control_type: ControlType | None = None
    _instruction_history: SizeLimitOrderedDict[str, BaseModel]
    _instruction_status_history: SizeLimitOrderedDict[str, InstructionStatus]
    _fm_client: FlexMeasuresClient
    _sending_queue: Queue
    _logger: Logger

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._instruction_history = SizeLimitOrderedDict(max_size=max_size)
        self._instruction_status_history = SizeLimitOrderedDict(max_size=max_size)
        # _logger was only ever an annotation, assigned by CEM.register_control_type(). A handler
        # used before registration - in a test, or on any path that logs during construction -
        # therefore raised AttributeError instead of logging. Default it here; the CEM still
        # replaces it with its own logger on registration.
        self._logger = getLogger(self.__class__.__name__)

    async def send_message(self, message: BaseModel) -> None:
        """Hand a message to the CEM, which sends it to the resource manager.

        Handlers send through this method rather than putting messages on _sending_queue themselves,
        so that how a message leaves the CEM is one decision in one place.
        """
        await self._sending_queue.put(message)

    async def close(self):
        """Release any resources / stop recurring tasks for this handler.

        Default no-op so CEM.close(), which calls close() on every registered handler when a
        websocket tears down, works for any control-type handler.
        Subclasses that own recurring tasks override this.
        Without it, a handler lacking close() raised AttributeError inside CEM.close(),
        killing the CEM's request handler and hanging the resource manager.
        """
        self._logger.debug(f"Closing {self.__class__.__name__} handler")

    @register(InstructionStatusUpdate)
    def handle_instruction_status_update(self, message: InstructionStatusUpdate):
        # instruction_id is a UUID, and the cast that claimed otherwise did nothing at runtime,
        # so the history was keyed by UUID objects while it declared string keys.
        # A lookup by the instruction's id, written as a string, therefore always missed.
        instruction_id = str(message.instruction_id)

        self._instruction_status_history[instruction_id] = message.status_type

        return get_reception_status(message, status=ReceptionStatusValues.OK)
