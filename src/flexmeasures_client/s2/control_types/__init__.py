from python_s2_protocol.common.schemas import ControlType

from flexmeasures_client.s2 import Handler


class ControlTypeHandler(Handler):
    control_type = ControlType.FILL_RATE_BASED_CONTROL
