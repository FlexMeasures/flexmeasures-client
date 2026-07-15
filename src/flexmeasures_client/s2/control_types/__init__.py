from __future__ import annotations

from logging import Logger
from typing import Callable, cast

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
    send_message: Callable
    _logger: Logger

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._instruction_history = SizeLimitOrderedDict(max_size=max_size)
        self._instruction_status_history = SizeLimitOrderedDict(max_size=max_size)

    async def close(self):
        """Release any resources / stop recurring tasks for this handler.

        Default no-op so CEM.close() (which calls close() on every registered
        handler when a websocket tears down) works for any control-type handler.
        Subclasses that own recurring tasks override this. Previously FRBCSimple
        had no close(), so a websocket teardown raised AttributeError inside
        CEM.close(), killing the CEM's request handler and hanging the RM.
        """
        self._logger.debug(f"Closing {self.__class__.__name__} handler")

    @register(InstructionStatusUpdate)
    def handle_instruction_status_update(self, message: InstructionStatusUpdate):
        instruction_id: str = cast(str, message.instruction_id)

        self._instruction_status_history[instruction_id] = message.status_type

        return get_reception_status(message, status=ReceptionStatusValues.OK)
