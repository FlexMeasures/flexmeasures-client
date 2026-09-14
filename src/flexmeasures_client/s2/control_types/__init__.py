from __future__ import annotations

from logging import Logger, getLogger
from typing import Any, Callable, cast

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
    #: Back-reference to the CEM that registered this handler, so a handler can
    #: await the CEM's flush_measurement_posts() barrier before triggering a
    #: schedule. Set by CEM.register_control_type().
    _cem: Any = None

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._instruction_history = SizeLimitOrderedDict(max_size=max_size)
        self._instruction_status_history = SizeLimitOrderedDict(max_size=max_size)
        # _logger was only ever an annotation, assigned by CEM.register_control_type(). A handler
        # used before registration - in a test, or on any path that logs during construction -
        # therefore raised AttributeError instead of logging. Default it here; the CEM still
        # replaces it with its own logger on registration.
        self._logger = getLogger(self.__class__.__name__)

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
