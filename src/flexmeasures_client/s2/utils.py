from collections import OrderedDict
from typing import Mapping, TypeVar
from uuid import uuid4

import pydantic

from flexmeasures_client.s2.python_s2_protocol.common.messages import ReceptionStatus
from flexmeasures_client.s2.python_s2_protocol.common.schemas import (
    ReceptionStatusValues,
)

KT = TypeVar("KT")
VT = TypeVar("VT")


class SizeLimitOrderedDict(OrderedDict, Mapping[KT, VT]):
    _max_size = None

    def __init__(self, *args, max_size=100, **kwargs):
        super(SizeLimitOrderedDict, self).__init__(*args, **kwargs)
        self._max_size = max_size

        # deleting values to make the dictionary of length _max_size at most
        while len(self) > self._max_size:
            self.popitem()

    def __setitem__(self, __key: KT, __value: VT) -> None:
        if len(self) == self._max_size:
            self.popitem()

        return super().__setitem__(__key, __value)


def get_unique_id() -> str:
    """Generate a random v4 UUID string.

    Why UUID4? UUID4 is a hash of a 122bit random number
    which means that, in practice, the probability of collision
    is very low (1 collision is 2.71 quintillion, src: Wikipedia).
    """
    return str(uuid4())


def get_validation_error_summary(error: pydantic.ValidationError) -> str:
    error_summary = ""

    for i, e in enumerate(error):
        error_summary += f"\nValidationEror {i} -> \t {e['msg']}"

    return error_summary[1:]  # skipping the first \n


def get_message_id(message: pydantic.BaseModel) -> str:
    """
    This function returns the message_id if it is found in the message,
    else it tries to get the subject_message_id, which is present in
    ReceptionStatus.
    """
    if hasattr(message, "message_id"):
        return message.message_id.__root__
    elif hasattr(message, "subject_message_id"):
        return message.subject_message_id.__root__


def get_reception_status(
    subject_message: pydantic.BaseModel,
    status: ReceptionStatusValues = ReceptionStatusValues.OK,
):
    """
    This function returns a ReceptionStatus message for the subject message
    `subject_message`. By default, the status ReceptionStatusValues.OK is sent.
    """
    return ReceptionStatus(
        subject_message_id=subject_message.message_id.__root__, status=status
    )
