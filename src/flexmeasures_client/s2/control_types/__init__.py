from asyncio import Queue
from typing import cast

from pydantic import BaseModel
from python_s2_protocol.common.messages import (
    InstructionStatus,
    InstructionStatusUpdate,
)
from python_s2_protocol.common.schemas import ControlType, ReceptionStatusValues

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.utils import SizeLimitOrderedDict, get_reception_status


class ControlTypeHandler(Handler):
    _control_type: ControlType = None
    _instruction_historic: SizeLimitOrderedDict[str, BaseModel]
    _instruction_status_historic: SizeLimitOrderedDict[str, InstructionStatus]
    _fm_client: FlexMeasuresClient
    _sending_queue: Queue

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._instruction_historic = SizeLimitOrderedDict(max_size=max_size)
        self._instruction_status_historic = SizeLimitOrderedDict(max_size=max_size)

    @register(InstructionStatusUpdate)
    def handle_instruction_status_update(self, message: InstructionStatusUpdate):
        instruction_id: str = cast(str, message.instruction_id.__root__)

        self._instruction_status_historic[instruction_id] = message.status_type

        return get_reception_status(message, status=ReceptionStatusValues.OK)
