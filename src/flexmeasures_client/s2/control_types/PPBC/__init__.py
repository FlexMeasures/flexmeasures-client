import pydantic
from s2python.common import ControlType, ReceptionStatusValues
from s2python.ppbc import PPBCEndInterruptionInstruction, PPBCScheduleInstruction

from flexmeasures_client.s2 import SizeLimitOrderedDict, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.utils import get_reception_status

# from flexmeasures_client.s2.utils import get_reception_status, get_unique_id


class PPBC(ControlTypeHandler):
    _control_type = ControlType.POWER_PROFILE_BASED_CONTROL

    _schedule_instruction_history: SizeLimitOrderedDict[str, PPBCScheduleInstruction]
    _end_interruption_instruction_history: SizeLimitOrderedDict[
        str, PPBCEndInterruptionInstruction
    ]

    def __init__(self, max_size: int = 100) -> None:
        super().__init__(max_size)

        self._schedule_instruction_history = SizeLimitOrderedDict(max_size=max_size)
        self._end_interruption_instruction_history = SizeLimitOrderedDict(
            max_size=max_size
        )

    @register(PPBCScheduleInstruction)
    def handle_schedule_instruction(
        self, message: PPBCScheduleInstruction
    ) -> pydantic.BaseModel:
        schedule_instruction_id = str(message.message_id)
        self._schedule_instruction_history[schedule_instruction_id] = message
        return get_reception_status(message, status=ReceptionStatusValues.OK)

    @register(PPBCEndInterruptionInstruction)
    def handle_end_interruption_instruction(
        self, message: PPBCEndInterruptionInstruction
    ) -> pydantic.BaseModel:
        end_interruption_instruction_id = str(message.message_id)
        self._end_interruption_instruction_history[
            end_interruption_instruction_id
        ] = message
        return get_reception_status(message, status=ReceptionStatusValues.OK)
