from python_s2_protocol.common.schemas import ControlType

from flexmeasures_client.s2 import Handler


class ControlTypeHandler(Handler):
    _control_type: ControlType = None
