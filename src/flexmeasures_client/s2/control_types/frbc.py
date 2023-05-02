from typing import List

import pydantic
from python_s2_protocol.common.messages import ReceptionStatus, ReceptionStatusValues
from python_s2_protocol.FRBC.messages import FRBCSystemDescription

from flexmeasures_client.s2 import register
from flexmeasures_client.s2.control_types import ControlTypeHandler


class FRBC(ControlTypeHandler):
    system_description_list: List[FRBCSystemDescription] = list()

    @register(FRBCSystemDescription, "FRBC.SystemDescription")
    def handle_system_description(
        self, message: pydantic.BaseModel
    ) -> pydantic.BaseModel:
        self.system_description_list.append(
            message
        )  # TODO: update system description / get valid ones
        return ReceptionStatus(
            subject_message_id=message.message_id, status=ReceptionStatusValues.OK
        )


"""
================================================================
Potential adapatations to persist the state in different systems
================================================================

class FRBCHomeAssistant(FRBC, HomeAssistantStateMixin):
    pass

class FRBCPostgres(FRBC, PostgreSQLMixing):
    pass

 """
