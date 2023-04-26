from collections import OrderedDict
from uuid import uuid1

import pydantic


class SizeLimitOrderedDict(OrderedDict):
    _max_size = None

    def __init__(self, *args, max_size=100, **kwargs):
        super(SizeLimitOrderedDict, self).__init__(*args, **kwargs)
        self._max_size = max_size

        # deleting values to make the dictionary of length _max_size at most
        while len(self) > self._max_size:
            self.popitem()

    def __setitem__(self, __key, __value) -> None:
        if len(self) == self._max_size:
            self.popitem()

        return super().__setitem__(__key, __value)


def get_unique_id() -> str:
    return str(uuid1())


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
