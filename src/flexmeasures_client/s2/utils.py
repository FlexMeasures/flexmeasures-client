from __future__ import annotations

from collections import OrderedDict
from typing import Mapping, TypeVar
from uuid import uuid4

import pydantic
import semver
from packaging.version import Version

try:
    from s2python.common import ReceptionStatus, ReceptionStatusValues
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
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


def get_message_id(message: pydantic.BaseModel) -> str | None:
    """
    This function returns the message_id if it is found in the message,
    else it tries to get the subject_message_id, which is present in
    ReceptionStatus.
    """
    if hasattr(message, "message_id"):
        return str(message.message_id)
    elif hasattr(message, "subject_message_id"):
        return str(message.subject_message_id)
    return None


def get_reception_status(
    subject_message: pydantic.BaseModel,
    status: ReceptionStatusValues = ReceptionStatusValues.OK,
):
    """
    This function returns a ReceptionStatus message for the subject message
    `subject_message`. By default, the status ReceptionStatusValues.OK is sent.
    """
    return ReceptionStatus(
        subject_message_id=str(subject_message.message_id), status=status
    )


def is_version_supported(v1: semver.Version, v2: semver.Version) -> bool:
    return v1 >= v2


def get_latest_compatible_version(supported_versions, current_version, logger):
    """
    Determines the latest compatible version based on supported protocol versions.

    :param supported_versions: List of supported protocol versions (strings).
    :param current_version: Current version of the system (string).
    :param logger: Optional logger instance.
    :return: Latest compatible version (Version object) or current_version if none
     found.
    """

    # RM didn't provide supported versions
    if not supported_versions or len(supported_versions) == 0:
        logger.warning("RM didn't provide any supported version")
        return Version(current_version)

    # Convert supported versions to Version objects and sort in descending order
    rm_versions = sorted(
        (semver.Version.parse(v) for v in supported_versions), reverse=True
    )
    cem_version = semver.Version.parse(current_version)

    # Find the latest compatible version
    latest_compatible_version = next(
        (v for v in rm_versions if is_version_supported(v, cem_version)), None
    )

    if latest_compatible_version is None:
        logger.warning(
            f"There are no compatible S2 versions supported both by the "
            f"RM ({rm_versions}) and CEM ({cem_version})"
        )
        return cem_version

    return latest_compatible_version
